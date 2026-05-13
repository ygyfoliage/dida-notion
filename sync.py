# sync.py
# Username + Password version - Syncs Dida365 tasks to Notion
# Uses email and password to login to Dida365

import requests
from datetime import date
from notion_client import Client
import os
import sys
import json

# Suppress SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================================
# Configuration from environment variables
# ================================
DIDA_USERNAME = os.environ.get("DIDA_USERNAME")  # Email
DIDA_PASSWORD = os.environ.get("DIDA_PASSWORD")  # Password
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

# Validate required secrets
if not all([DIDA_USERNAME, DIDA_PASSWORD, NOTION_TOKEN, NOTION_DATABASE_ID]):
    print("Error: Missing required environment variables!")
    print(f"  DIDA_USERNAME: {bool(DIDA_USERNAME)}")
    print(f"  DIDA_PASSWORD: {bool(DIDA_PASSWORD)}")
    print(f"  NOTION_TOKEN: {bool(NOTION_TOKEN)}")
    print(f"  NOTION_DATABASE_ID: {bool(NOTION_DATABASE_ID)}")
    sys.exit(1)


# ================================
# Step 1: Login to Dida365
# ================================
def login_dida():
    print("Logging in to Dida365...")
    session = requests.Session()
    
    try:
        resp = session.post(
            "https://api.dida365.com/api/v2/user/signon?wc=true&remember=true",
            json={
                "username": DIDA_USERNAME,
                "password": DIDA_PASSWORD
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            },
            verify=False,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Login failed. Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        sys.exit(1)

    try:
        data = resp.json()
        token = data.get("token")
        if not token:
            print(f"No token in response: {data}")
            sys.exit(1)
    except ValueError:
        print(f"Invalid JSON response: {resp.text}")
        sys.exit(1)

    print("✓ Login successful")
    return session, token


# ================================
# Step 2: Get tasks from Dida365
# ================================
def get_due_today_tasks(session, token):
    today = date.today().isoformat()  # e.g. "2026-05-13"
    print(f"Fetching tasks due on {today}...")

    headers = {
        "Cookie": f"t={token}",
        "User-Agent": "Mozilla/5.0",
        "x-device": '{"platform":"web","os":"macOS","device":"Chrome","name":"","version":4531,"id":"abcd1234","channel":"website","campaign":"","websocket":""}'
    }

    try:
        resp = session.get(
            "https://api.dida365.com/api/v2/batch/check/0",
            headers=headers,
            verify=False,
            timeout=10
        )
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    if resp.status_code == 401:
        print("Error: Invalid credentials or session expired")
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
        due_date = task.get("dueDate", "")  # e.g. "2026-05-13T16:00:00.000+0000"
        status = task.get("status", -1)      # 0 = incomplete, 2 = completed
        if due_date.startswith(today) and status == 0:
            due_today.append(task)

    print(f"Tasks due today: {len(due_today)}")
    return due_today, today


# ================================
# Step 3: Get existing Notion tasks (for deduplication)
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
# Step 4: Sync tasks to Notion
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
    print("Dida365 to Notion Sync (Username + Password)")
    print("=" * 50 + "\n")

    # Step 1: Login to Dida365
    try:
        session, token = login_dida()
    except Exception as e:
        print(f"Error logging in: {e}")
        sys.exit(1)

    # Step 2: Get tasks from Dida365
    try:
        due_today, today = get_due_today_tasks(session, token)
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        sys.exit(1)

    if not due_today:
        print("\nNo tasks due today. Exiting.")
        return

    # Step 3: Initialize Notion client and check existing tasks
    try:
        notion = Client(auth=NOTION_TOKEN)
        existing_titles = get_existing_notion_tasks(notion)
    except Exception as e:
        print(f"Error connecting to Notion: {e}")
        sys.exit(1)

    # Step 4: Sync to Notion
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
    main()
