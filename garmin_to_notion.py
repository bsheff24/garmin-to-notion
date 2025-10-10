import os
from datetime import date, datetime
from notion_client import Client
from garminconnect import Garmin

# Load environment variables
GARMIN_USERNAME = os.environ.get("GARMIN_USERNAME")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.environ.get("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.environ.get("NOTION_ACTIVITIES_DB_ID")
NOTION_STEPS_DB_ID = os.environ.get("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.environ.get("NOTION_SLEEP_DB_ID")
NOTION_PR_DB_ID = os.environ.get("NOTION_PR_DB_ID")

# Connect to Notion
notion = Client(auth=NOTION_TOKEN)

# Connect to Garmin
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # Works in GitHub workflow

# ----------------------
# Helper functions
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

today = date.today()
today_str = today.strftime("%Y-%m-%d")

# ----------------------
# 1. Pull Garmin data
# ----------------------
# Activities (latest only)
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},
        "Duration (min)": {"number": round(act.get("duration", 0)/60, 1)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mile(act.get("averageSpeed",0) and 60/act["averageSpeed"] or 0)}
    }

# Steps
steps_data = garmin_client.get_daily_steps(today_str)
steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_data.get("steps", 0)}
}

# Sleep
sleep_data = garmin_client.get_sleep_data(today_str)
sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Sleep Score": {"number": sleep_data.get("sleepScore", 0)},
    "Bedtime": {"rich_text": [{"text": {"content": sleep_data.get("startTimeLocal", "")}}]},
    "Wake Time": {"rich_text": [{"text": {"content": sleep_data.get("endTimeLocal", "")}}]}
}

# Body metrics
body_stats = garmin_client.get_stats_and_body(today_str)
health_row = {
    "Date": {"date": {"start": today_str}},
    "Bodyweight (lb)": {"number": round(body_stats.get("weight", 0) * 2.20462, 1)},
    "Body Battery": {"number": body_stats.get("bodyBattery", 0)},
    "Training Readiness": {"number": body_stats.get("trainingReadiness", 0)},
    "Training Status": {"rich_text": [{"text": {"content": body_stats.get("trainingStatus", "")}}]}
}

# Personal Records
pr_list = garmin_client.get_personal_record()
pr_row = {
    "Date": {"date": {"start": today_str}}
}
for pr in pr_list:
    pr_row[pr["activityType"]] = {"number": pr["distance"]}  # Adjust property type in Notion if needed

# ----------------------
# 2. Push to Notion
# ----------------------
def push_to_notion(row, db_id, name="row"):
    if row and not already_logged(db_id, row["Date"]["date"]["start"]):
        notion.pages.create(parent={"database_id": db_id}, properties=row)
        print(f"✅ {name} added for {row['Date']['date']['start']}")
    else:
        print(f"⚠️ {name} already logged or missing")

push_to_notion(activity_row, NOTION_ACTIVITIES_DB_ID, "Activity")
push_to_notion(steps_row, NOTION_STEPS_DB_ID, "Steps")
push_to_notion(sleep_row, NOTION_SLEEP_DB_ID, "Sleep")
push_to_notion(health_row, NOTION_HEALTH_DB_ID, "Health metrics")
push_to_notion(pr_row, NOTION_PR_DB_ID, "Personal records")

