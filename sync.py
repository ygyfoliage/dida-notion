import requests
from datetime import date
from notion_client import Client
import os

# ================================
# 配置区域 - 从环境变量读取
# ================================
DIDA_USERNAME = os.environ.get("DIDA_USERNAME")   # 滴答清单 邮箱
DIDA_PASSWORD = os.environ.get("DIDA_PASSWORD")   # 滴答清单 密码
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")     # Notion API Token
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")  # Notion 数据库 ID

# ================================
# Step 1: 登录滴答清单
# ================================
def login_dida():
    session = requests.Session()
    print("正在登录滴答清单...")

    resp = session.post(
        "https://api.dida365.com/api/v2/user/signon?wc=true&remember=true",
        json={
            "username": DIDA_USERNAME,
            "password": DIDA_PASSWORD
        },
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
    )

    if resp.status_code != 200:
        raise Exception(f"登录失败，状态码: {resp.status_code}，返回: {resp.text}")

    data = resp.json()
    token = data.get("token")
    if not token:
        raise Exception(f"未获取到 token，返回内容: {data}")

    print("✅ 登录成功")
    return session, token


# ================================
# Step 2: 获取今日截止任务
# ================================
def get_due_today_tasks(session, token):
    today = date.today().isoformat()  # e.g. "2024-05-12"
    print(f"正在获取截止日期为 {today} 的任务...")

    headers = {
        "Cookie": f"t={token}",
        "User-Agent": "Mozilla/5.0",
        "x-device": '{"platform":"web","os":"macOS","device":"Chrome","name":"","version":4531,"id":"abcd1234","channel":"website","campaign":"","websocket":""}'
    }

    resp = session.get(
        "https://api.dida365.com/api/v2/batch/check/0",
        headers=headers
    )

    if resp.status_code != 200:
        raise Exception(f"获取任务失败，状态码: {resp.status_code}")

    all_tasks = resp.json().get("syncTaskBean", {}).get("update", [])
    print(f"共获取到 {len(all_tasks)} 条任务，开始筛选今日截止...")

    due_today = []
    for task in all_tasks:
        due_date = task.get("dueDate", "")         # e.g. "2024-05-12T16:00:00.000+0000"
        status = task.get("status", -1)             # 0 = 未完成, 2 = 已完成
        if due_date.startswith(today) and status == 0:
            due_today.append(task)

    print(f"✅ 找到 {len(due_today)} 条今日截止任务")
    return due_today, today


# ================================
# Step 3: 检查 Notion 中是否已存在（去重）
# ================================
def get_existing_notion_tasks(notion):
    print("正在读取 Notion 数据库中已有任务（用于去重）...")
    existing_titles = set()

    results = notion.databases.query(database_id=NOTION_DATABASE_ID)
    for page in results.get("results", []):
        try:
            title = page["properties"]["Name"]["title"][0]["text"]["content"]
            existing_titles.add(title)
        except (KeyError, IndexError):
            pass

    print(f"✅ Notion 中已有 {len(existing_titles)} 条任务")
    return existing_titles


# ================================
# Step 4: 写入 Notion
# ================================
def sync_to_notion(tasks, today, notion, existing_titles):
    print("开始同步到 Notion...")
    synced_count = 0
    skipped_count = 0

    for task in tasks:
        title = task.get("title", "（无标题）")

        # 去重：已存在则跳过
        if title in existing_titles:
            print(f"  ⏭️  跳过（已存在）: {title}")
            skipped_count += 1
            continue

        # 获取任务优先级（滴答清单：0=无, 1=低, 3=中, 5=高）
        priority_map = {0: "无", 1: "低", 3: "中", 5: "高"}
        priority = priority_map.get(task.get("priority", 0), "无")

        # 获取任务备注
        content = task.get("content", "")

        try:
            notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties={
                    "Name": {
                        "title": [{"text": {"content": title}}]
                    },
                    "Due Date": {
                        "date": {"start": today}
                    },
                    "Priority": {
                        "select": {"name": priority}
                    },
                    "Status": {
                        "select": {"name": "待完成"}
                    },
                    "Notes": {
                        "rich_text": [{"text": {"content": content[:2000]}}]  # Notion 限制 2000 字符
                    }
                }
            )
            print(f"  ✅ 已同步: {title}")
            synced_count += 1

        except Exception as e:
            print(f"  ❌ 同步失败: {title}，错误: {e}")

    print(f"\n🎉 同步完成！新增 {synced_count} 条，跳过 {skipped_count} 条")


# ================================
# 主函数
# ================================
def main():
    # 检查环境变量
    missing = []
    if not DIDA_USERNAME:
        missing.append("DIDA_USERNAME")
    if not DIDA_PASSWORD:
        missing.append("DIDA_PASSWORD")
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if not NOTION_DATABASE_ID:
        missing.append("NOTION_DATABASE_ID")

    if missing:
        raise Exception(f"缺少环境变量: {', '.join(missing)}")

    # 初始化 Notion 客户端
    notion = Client(auth=NOTION_TOKEN)

    # 执行同步流程
    session, token = login_dida()
    tasks, today = get_due_today_tasks(session, token)

    if not tasks:
        print("📭 今日没有截止任务，无需同步")
        return

    existing_titles = get_existing_notion_tasks(notion)
    sync_to_notion(tasks, today, notion, existing_titles)


if __name__ == "__main__":
    main()