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
garmin_client.login()  # legacy bypass works on GitHub workflow

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

# ----------------------------
# Dates
# ----------------------------
today_str = datetime.now().strftime("%Y-%m-%d")

# ----------------------------
# 1. Garmin data
# ----------------------------
# Latest activity
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},
        "Duration (min)": {"number": round(act.get("duration",0)/60,1)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mile(act.get("averageSpeed",0) and 60/act["averageSpeed"] or 0)}
    }

# Daily stats
daily_summary = garmin_client.get_stats(today_str)
health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": daily_summary.get("steps", 0)},
    "Sleep Score": {"number": daily_summary.get("sleepScore", 0)},
    "Bodyweight (lb)": {"number": daily_summary.get("weight", 0) * 2.20462}
}

# Steps
steps_list = garmin_client.get_daily_steps(today_str, today_str)
steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_list[0].get("steps", 0) if steps_list else 0}
}

# Sleep
sleep_list = garmin_client.get_sleep_data(today_str)
sleep_row = {}
if sleep_list:
    sleep = sleep_list[0]
    sleep_row = {
        "Date": {"date": {"start": today_str}},
        "Bedtime": {"rich_text": [{"text": {"content": sleep.get("startTime", "")}}]},
        "Wake Time": {"rich_text": [{"text": {"content": sleep.get("endTime", "")}}]},
        "Sleep Score": {"number": sleep.get("sleepScore", 0)}
    }

# Body Battery
body_battery = garmin_client.get_body_battery(today_str)
body_row = {
    "Date": {"date": {"start": today_str}},
    "Body Battery": {"number": body_battery.get("bodyBattery", 0)}
}

# Personal records
pr_list = garmin_client.get_personal_record()
pr_rows = []
for pr in pr_list:
    pr_rows.append({
        "Date": {"date": {"start": today_str}},
        "Record Type": {"rich_text": [{"text": {"content": pr.get("typeName","")}}]},
        "Value": {"number": pr.get("value",0)}
    })

# ----------------------------
# 2. Push to Notion
# ----------------------------
# Activities
if activity_row and not already_logged(NOTION_ACTIVITIES_DB_ID, activity_row["Date"]["date"]["start"]):
    notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_row)
    print(f"✅ Activity added for {activity_row['Date']['date']['start']}")

# Health Metrics
if health_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_row)
    print(f"✅ Health metrics added for {today_str}")

# Steps
if steps_row and not already_logged(NOTION_STEPS_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_STEPS_DB_ID}, properties=steps_row)
    print(f"✅ Steps added for {today_str}")

# Sleep
if sleep_row and not already_logged(NOTION_SLEEP_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_SLEEP_DB_ID}, properties=sleep_row)
    print(f"✅ Sleep added for {today_str}")

# Body Battery
if body_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=body_row)
    print(f"✅ Body battery added for {today_str}")

# Personal Records
for pr_row in pr_rows:
    notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=pr_row)
    print(f"✅ Personal record added for {today_str}: {pr_row['Record Type']['rich_text'][0]['text']['content']}")
