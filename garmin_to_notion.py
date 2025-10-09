import os
from datetime import datetime, date
from notion_client import Client
from garminconnect import Garmin

# ----------------------
# Load environment variables
# ----------------------
GARMIN_USERNAME = os.environ.get("GARMIN_USERNAME")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.environ.get("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.environ.get("NOTION_ACTIVITIES_DB_ID")
NOTION_PR_DB_ID = os.environ.get("NOTION_PR_DB_ID")
NOTION_STEPS_DB_ID = os.environ.get("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.environ.get("NOTION_SLEEP_DB_ID")

# ----------------------
# Connect to Notion
# ----------------------
notion = Client(auth=NOTION_TOKEN)

# ----------------------
# Connect to Garmin
# ----------------------
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # Works on GitHub workflow

# ----------------------
# Helpers
# ----------------------
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

# ----------------------
# Pull Garmin Data
# ----------------------
today_str = datetime.now().strftime("%Y-%m-%d")

# 1. Activities
activities = garmin_client.get_activities(1)  # latest activity only
activity_row = {}
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},
        "Duration (min)": {"number": round(act.get("duration",0)/60,1)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mile(act.get("averageSpeed",0) and 60/act["averageSpeed"] or 0)}
    }

# 2. Daily Stats / Health Metrics
daily_summary = garmin_client.get_stats(today_str)  # Returns daily summary dict
body_metrics = garmin_client.get_body()            # Returns body metrics dict
sleep_data = garmin_client.get_sleep_data(today_str, today_str)  # List of sleep periods

health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": daily_summary.get("steps", 0)},
    "Sleep Score": {"number": daily_summary.get("sleepScore", 0)},
    "Bodyweight (lb)": {"number": daily_summary.get("weight", 0) * 2.20462},
    "Body Battery": {"number": body_metrics.get("bodyBattery", 0)},
    "Training Readiness": {"number": daily_summary.get("trainingReadinessScore", 0)},
    "Training Status": {"rich_text": [{"text": {"content": daily_summary.get("trainingStatus", "")}}]}
}

if sleep_data:
    health_row["Bed Time"] = {"date": {"start": sleep_data[0]["startTimeLocal"]}}
    health_row["Wake Time"] = {"date": {"start": sleep_data[0]["endTimeLocal"]}}

# 3. Personal Records
prs = garmin_client.get_personal_records()  # Returns dict of PRs

pr_rows = []
for pr_name, pr_value in prs.items():
    pr_rows.append({
        "Record": {"rich_text": [{"text": {"content": pr_name}}]},
        "Value": {"number": pr_value},
        "Date": {"date": {"start": today_str}}
    })

# ----------------------
# Push to Notion
# ----------------------
# Activities DB
if activity_row and not already_logged(NOTION_ACTIVITIES_DB_ID, activity_row["Date"]["date"]["start"]):
    notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_row)
    print(f"✅ Activity added for {activity_row['Date']['date']['start']}")
else:
    print(f"⚠️ Activity already logged or missing")

# Health Metrics DB
if health_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_row)
    print(f"✅ Health metrics added for {today_str}")
else:
    print(f"⚠️ Health metrics already logged or missing")

# Personal Records DB
for pr_row in pr_rows:
    if not already_logged(NOTION_PR_DB_ID, pr_row["Date"]["date"]["start"]):
        notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=pr_row)
        print(f"✅ PR added: {pr_row['Record']['rich_text'][0]['text']['content']}")
