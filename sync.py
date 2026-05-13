# sync.py
# OAuth2 version - uses access_token from GitHub Secrets
# Syncs Dida365 tasks with due date = today to Notion

import requests
from datetime import date
from notion_client import Client
import os
import sys
import random
import time

# ================================
# Configuration from environment variables
# ================================
DIDA_ACCESS_TOKEN = os.environ.get("DIDA_ACCESS_TOKEN")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
USE_PROXY = os.environ.get("USE_PROXY", "true").lower() == "true"

# Free proxy pool sources
PROXY_SOURCES = [
    "https://www.proxy-list.download/api/v1/get?type=http",  # Proxy-List.Download
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",  # GitHub proxy list
]

proxy_list = []

# Validate required secrets
if not all([DIDA_ACCESS_TOKEN, NOTION_TOKEN, NOTION_DATABASE_ID]):
    print("Error: Missing required environment variables!")
    print(f"  DIDA_ACCESS_TOKEN: {bool(DIDA_ACCESS_TOKEN)}")
    print(f"  NOTION_TOKEN: {bool(NOTION_TOKEN)}")
    print(f"  NOTION_DATABASE_ID: {bool(NOTION_DATABASE_ID)}")
    sys.exit(1)

# ================================
# Proxy management
# ================================
def fetch_proxies():
    """Fetch free proxies from multiple sources"""
    global proxy_list
    print("Fetching free proxies...")
    
    for source in PROXY_SOURCES:
        try:
            resp = requests.get(source, timeout=5, verify=False)
            if resp.status_code == 200:
                lines = resp.text.strip().split('\n')
                for line in lines[:20]:  # Take first 20 proxies
                    line = line.strip()
                    if line and ':' in line:
                        proxy_list.append(f"http://{line}")
                print(f"Fetched {len(lines)} proxies from {source}")
                break
        except Exception as e:
            print(f"Failed to fetch from {source}: {e}")
            continue
    
    if proxy_list:
        print(f"Total proxies available: {len(proxy_list)}")
    else:
        print("Warning: No proxies fetched, will try without proxy")
    
    return proxy_list


def get_random_proxy():
    """Get a random proxy from the list"""
    if not proxy_list:
        return None
    return random.choice(proxy_list)


def make_request_with_retry(url, method="get", headers=None, data=None, params=None, max_retries=3):
    """Make HTTP request with proxy and retry logic"""
    for attempt in range(max_retries):
        try:
            proxy = get_random_proxy() if USE_PROXY else None
            proxies = {"http": proxy, "https": proxy} if proxy else {}
            
            if proxy:
                print(f"Attempt {attempt + 1}: Using proxy {proxy}")
            
            if method == "get":
                resp = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    proxies=proxies,
                    verify=False,
                    timeout=10
                )
            else:  # post
                resp = requests.post(
                    url,
                    headers=headers,
                    data=data,
                    proxies=proxies,
                    verify=False,
                    timeout=10
                )
            
            return resp
        except Exception as e:
            print(f"Request failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)  # Wait before retry
            continue
    
    return None


# ================================
# Step 1: Get tasks from Dida365 using OAuth2
# ================================
def get_due_today_tasks():
    today = date.today().isoformat()  # e.g. "2026-05-12"
    print(f"Fetching tasks due on {today}...")

    headers = {
        "Authorization": f"Bearer {DIDA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        resp = make_request_with_retry(
            "https://api.dida365.com/api/v2/batch/check/0",
            method="get",
            headers=headers
        )
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    if resp.status_code == 401:
        print("Error: Access token expired or invalid!")
        print("Please run get_token.py again to refresh the token.")
        sys.exit(1)
    elif resp.status_code != 200:
        print(f"Error: Failed to fetch tasks. Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        sys.exit(1)

    try:
        all_tasks = resp.json().get("syncTaskBean", {}).get("update", [])
    except ValueError:
        print(f"Error: Invalid JSON response: {resp.text}")
        sys.exit(1)

    print(f"Total tasks fetched: {len(all_tasks)}")

    # Filter tasks due today and not completed
    due_today = []
    for task in all_tasks:
        due_date = task.get("dueDate", "")  # e.g. "2026-05-12T16:00:00.000+0000"
        status = task.get("status", -1)      # 0 = incomplete, 2 = completed
        if due_date.startswith(today) and status == 0:
            due_today.append(task)

    print(f"Tasks due today: {len(due_today)}")
    return due_today, today


# ================================
# Step 2: Get existing Notion tasks (for deduplication)
# ================================
def get_existing_notion_tasks(notion):
    print("Checking existing Notion tasks...")
    existing_titles = set()

    try:
        results = notion.databases.query(database_id=NOTION_DATABASE_ID)
        for page in results.get("results", []):
            try:
                title = page["properties"]["Name"]["title"][0]["text"]["content"]
                existing_titles.add(title)
            except (KeyError, IndexError, TypeError):
                pass
    except Exception as e:
        print(f"Warning: Could not fetch existing Notion tasks: {e}")

    print(f"Existing Notion tasks: {len(existing_titles)}")
    return existing_titles


# ================================
# Step 3: Sync tasks to Notion
# ================================
def sync_to_notion(tasks, today, existing_titles):
    notion = Client(auth=NOTION_TOKEN)
    synced_count = 0
    skipped_count = 0

    for task in tasks:
        title = task.get("title", "Untitled")
        project_id = task.get("projectId", "")
        priority = task.get("priority", 0)  # 0=none, 1=low, 3=medium, 5=high

        # Skip if already in Notion
        if title in existing_titles:
            print(f"  ⊘ Skipped (already exists): {title}")
            skipped_count += 1
            continue

        # Map priority
        priority_map = {
            0: "None",
            1: "Low",
            3: "Medium",
            5: "High"
        }
        priority_label = priority_map.get(priority, "None")

        try:
            notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties={
                    "Name": {"title": [{"text": {"content": title}}]},
                    "Due Date": {"date": {"start": today}},
                    "Priority": {"select": {"name": priority_label}},
                }
            )
            print(f"  ✓ Synced: {title}")
            synced_count += 1
        except Exception as e:
            print(f"  ✗ Failed to sync '{title}': {e}")

    return synced_count, skipped_count


# ================================
# Main
# ================================
def main():
    print("\n" + "=" * 50)
    print("Dida365 to Notion Sync (OAuth2 + Proxy)")
    print("=" * 50 + "\n")
    
    # Fetch proxies if enabled
    if USE_PROXY:
        fetch_proxies()
    else:
        print("Proxy disabled")

    # Step 1: Get tasks from Dida365
    try:
        due_today, today = get_due_today_tasks()
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        sys.exit(1)

    if not due_today:
        print("\nNo tasks due today. Exiting.")
        return

    # Step 2: Initialize Notion client and check existing tasks
    try:
        notion = Client(auth=NOTION_TOKEN)
        existing_titles = get_existing_notion_tasks(notion)
    except Exception as e:
        print(f"Error connecting to Notion: {e}")
        sys.exit(1)

    # Step 3: Sync to Notion
    print("\nSyncing tasks to Notion...\n")
    synced, skipped = sync_to_notion(due_today, today, existing_titles)

    # Summary
    print("\n" + "=" * 50)
    print(f"Sync completed!")
    print(f"  Synced: {synced}")
    print(f"  Skipped (already exists): {skipped}")
    print(f"  Total: {synced + skipped}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    main()
