import os
from datetime import datetime
from notion_client import Client
from garminconnect import Garmin

# ----------------------------
# Load environment variables
# ----------------------------
GARMIN_USERNAME = os.environ.get("GARMIN_USERNAME")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.environ.get("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.environ.get("NOTION_ACTIVITIES_DB_ID")
NOTION_STEPS_DB_ID = os.environ.get("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.environ.get("NOTION_SLEEP_DB_ID")
NOTION_PR_DB_ID = os.environ.get("NOTION_PR_DB_ID")

# ----------------------------
# Connect to services
# ----------------------------
notion = Client(auth=NOTION_TOKEN)

garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()

# ----------------------------
# Helper functions
# ----------------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def already_logged(db_id, date_str):
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Date", "date": {"equals": date_str}}
    )
    return len(results.get("results", [])) > 0

def safe_get(data, key, default=None):
    """Safely get nested dict values without crashing."""
    if isinstance(data, dict):
        return data.get(key, default)
    return default

# ----------------------------
# Dates
# ----------------------------
today_str = datetime.now().strftime("%Y-%m-%d")

# ----------------------------
# 1. Garmin Data Retrieval
# ----------------------------
# Latest activity
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal", today_str)[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0) / 1000)},
        "Duration (min)": {"number": round(act.get("duration", 0) / 60, 1)},
        "Avg Pace (min/mi)": {
            "number": min_per_km_to_min_per_mile(60 / act["averageSpeed"]) if act.get("averageSpeed") else 0
        }
    }

# Daily summary
try:
    daily_summary = garmin_client.get_stats(today_str)
except Exception:
    daily_summary = {}

health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": safe_get(daily_summary, "steps", 0)},
    "Sleep Score": {"number": safe_get(daily_summary, "sleepScore", 0)},
    "Bodyweight (lb)": {"number": safe_get(daily_summary, "weight", 0) * 2.20462 if safe_get(daily_summary, "weight") else 0}
}

# Steps
try:
    steps_list = garmin_client.get_daily_steps(today_str, today_str)
    steps_total = 0
    if isinstance(steps_list, list) and steps_list:
        steps_total = steps_list[0].get("steps", 0)
    elif isinstance(steps_list, dict):
        steps_total = steps_list.get("steps", 0)
except Exception:
    steps_total = 0

steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_total}
}

# Sleep
try:
    sleep_data = garmin_client.get_sleep_data(today_str)
except Exception:
    sleep_data = {}

sleep_row = {}
if sleep_data:
    start = safe_get(sleep_data, "sleepStartTimestampLocal", "")
    end = safe_get(sleep_data, "sleepEndTimestampLocal", "")
    score = safe_get(sleep_data, "sleepScoreFeedback", {}).get("sleepScore", 0)
    sleep_row = {
        "Date": {"date": {"start": today_str}},
        "Bedtime": {"rich_text": [{"text": {"content": start}}]},
        "Wake Time": {"rich_text": [{"text": {"content": end}}]},
        "Sleep Score": {"number": score}
    }

# Body Battery
try:
    body_battery = garmin_client.get_body_battery(today_str)
except Exception:
    body_battery = {}

body_row = {
    "Date": {"date": {"start": today_str}},
    "Body Battery": {"number": safe_get(body_battery, "bodyBattery", 0)}
}

# Personal Records
try:
    pr_list = garmin_client.get_personal_record()
except Exception:
    pr_list = []

pr_rows = []
if isinstance(pr_list, list):
    for pr in pr_list:
        pr_rows.append({
            "Date": {"date": {"start": today_str}},
            "Record Type": {"rich_text": [{"text": {"content": pr.get("typeName", "")}}]},
            "Value": {"number": pr.get("value", 0)}
        })

# ----------------------------
# 2. Push to Notion
# ----------------------------
def push_to_notion(db_id, row, label):
    if not db_id or not row:
        print(f"⚠️ Skipped {label} - missing data or DB ID")
        return
    date = row["Date"]["date"]["start"]
    if already_logged(db_id, date):
        print(f"⏩ {label} already logged for {date}")
        return
    notion.pages.create(parent={"database_id": db_id}, properties=row)
    print(f"✅ Added {label} for {date}")

push_to_notion(NOTION_ACTIVITIES_DB_ID, activity_row, "Activity")
push_to_notion(NOTION_HEALTH_DB_ID, health_row, "Health")
push_to_notion(NOTION_STEPS_DB_ID, steps_row, "Steps")
push_to_notion(NOTION_SLEEP_DB_ID, sleep_row, "Sleep")
push_to_notion(NOTION_HEALTH_DB_ID, body_row, "Body Battery")

for pr_row in pr_rows:
    push_to_notion(NOTION_PR_DB_ID, pr_row, f"PR ({pr_row['Record Type']['rich_text'][0]['text']['content']})")

print("🏁 Sync complete.")
