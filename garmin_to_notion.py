import os
import datetime
from garminconnect import Garmin
from notion_client import Client
from notion_client.errors import APIResponseError

# -----------------------------
# Helper Functions
# -----------------------------

def build_notion_date(dt):
    """Convert datetime or string into Notion date format."""
    if not dt:
        return {"date": None}
    if isinstance(dt, datetime.datetime):
        return {"date": {"start": dt.isoformat()}}
    if isinstance(dt, str):
        try:
            # Try to ensure valid ISO string
            datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
            return {"date": {"start": dt}}
        except Exception:
            pass
    return {"date": None}


def already_logged(notion, db_id, date):
    """Check if entry already exists for a given date."""
    try:
        results = notion.databases.query(
            **{
                "database_id": db_id,
                "filter": {
                    "property": "Date",
                    "date": {
                        "equals": date
                    }
                }
            }
        )
        return len(results.get("results", [])) > 0
    except APIResponseError as e:
        print(f"⚠️ Error checking for existing entries: {e}")
        return False


def push_to_notion(notion, db_id, data, label):
    """Push a new row to Notion if not already logged."""
    date = data["Date"]["date"]["start"]
    if already_logged(notion, db_id, date):
        print(f"⏩ {label} already logged for {date}")
        return
    try:
        notion.pages.create(parent={"database_id": db_id}, properties=data)
        print(f"✅ Added {label} for {date}")
    except Exception as e:
        print(f"❌ Failed to add {label}: {e}")


# -----------------------------
# Main Logic
# -----------------------------

garmin_user = os.getenv("GARMIN_USERNAME")
garmin_pass = os.getenv("GARMIN_PASSWORD")
notion_token = os.getenv("NOTION_TOKEN")

NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
NOTION_STEPS_DB_ID = os.getenv("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.getenv("NOTION_SLEEP_DB_ID")
NOTION_PR_DB_ID = os.getenv("NOTION_PR_DB_ID")

notion = Client(auth=notion_token)

garmin_client = Garmin(garmin_user, garmin_pass)
garmin_client.login()

today = datetime.date.today()
today_str = today.isoformat()

print("📡 Fetching data from Garmin...")

def safe_fetch(fetch_func, *args, **kwargs):
    try:
        return fetch_func(*args, **kwargs)
    except Exception as e:
        print(f"⚠️ Failed to fetch {fetch_func.__name__}: {e}")
        return None

body_battery = safe_fetch(garmin_client.get_body_battery, today_str)
training_readiness = safe_fetch(garmin_client.get_training_readiness, today_str)
training_status = safe_fetch(garmin_client.get_training_status, today_str)
steps_data = safe_fetch(garmin_client.get_daily_steps, today_str, today_str)
sleep_data = safe_fetch(garmin_client.get_sleep_data, today_str)
pr_data = safe_fetch(garmin_client.get_personal_record)

# -----------------------------
# Sleep Data Handling
# -----------------------------

sleep = sleep_data.get("dailySleepDTO") if isinstance(sleep_data, dict) else None
sleep_score = sleep.get("sleepScores", {}).get("overall", {}).get("value") if sleep else None
bedtime = sleep.get("sleepStartTimestampLocal") if sleep else None
wake_time = sleep.get("sleepEndTimestampLocal") if sleep else None
duration_secs = sleep.get("sleepTimeSeconds", 0) if sleep else 0
duration_hours = round(duration_secs / 3600, 2) if duration_secs else 0

sleep_row = {
    "Name": {"title": [{"text": {"content": f"Sleep — {today_str}"}}]},
    "Date": build_notion_date(today),
    "Sleep Score": {"number": sleep_score},
    "Duration (Hours)": {"number": duration_hours},
    "Bedtime": build_notion_date(bedtime),
    "Wake Time": build_notion_date(wake_time),
}

# -----------------------------
# Health Data
# -----------------------------

health_row = {
    "Name": {"title": [{"text": {"content": f"Health — {today_str}"}}]},
    "Date": build_notion_date(today),
    "Body Battery": {"number": body_battery.get("bodyBatteryValue", 0) if body_battery else 0},
    "Training Readiness": {"number": training_readiness.get("trainingReadinessScore", 0) if training_readiness else 0},
    "Training Status": {"rich_text": [{"text": {"content": training_status.get("trainingStatus", "Unknown")}}] if training_status else []},
}

# -----------------------------
# Steps Data
# -----------------------------

steps_total = 0
if isinstance(steps_data, list) and len(steps_data) > 0:
    steps_total = steps_data[0].get("totalSteps", 0)

steps_row = {
    "Name": {"title": [{"text": {"content": f"Steps — {today_str}"}}]},
    "Date": build_notion_date(today),
    "Steps": {"number": steps_total},
}

# -----------------------------
# Personal Records Data
# -----------------------------

pr_row = {
    "Name": {"title": [{"text": {"content": f"PR — {today_str}"}}]},
    "Date": build_notion_date(today),
}

if isinstance(pr_data, list):
    for item in pr_data:
        name = item.get("typeKey", "Unknown")
        value = item.get("value", 0)
        if isinstance(value, (int, float)):
            pr_row[name] = {"number": value}
        elif isinstance(value, str):
            pr_row[name] = {"rich_text": [{"text": {"content": value}}]}

# -----------------------------
# Push to Notion
# -----------------------------

print("🚀 Pushing to Notion...")

if health_row: 
    push_to_notion(notion, NOTION_HEALTH_DB_ID, health_row, "Health")

if steps_row:
    push_to_notion(notion, NOTION_STEPS_DB_ID, steps_row, "Steps")

if sleep_row and (sleep_score or duration_hours > 0):
    push_to_notion(notion, NOTION_SLEEP_DB_ID, sleep_row, "Sleep")
else:
    print("⚠️ No valid sleep data found, skipping Sleep push.")

if pr_row and len(pr_row.keys()) > 2:
    push_to_notion(notion, NOTION_PR_DB_ID, pr_row, "PR")
else:
    print("⚠️ No new personal records found, skipping PR push.")

garmin_client.logout()
print("✅ Sync complete.")
