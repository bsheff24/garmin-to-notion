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
NOTION_STEPS_DB_ID = os.environ.get("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.environ.get("NOTION_SLEEP_DB_ID")
NOTION_PR_DB_ID = os.environ.get("NOTION_PR_DB_ID")

# ----------------------
# Connect to Notion
# ----------------------
notion = Client(auth=NOTION_TOKEN)

# ----------------------
# Connect to Garmin
# ----------------------
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()

# ----------------------
# Helpers
# ----------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def build_notion_date(dt):
    """Return a properly formatted Notion date object."""
    if dt:
        if isinstance(dt, str):
            return {"date": {"start": dt}}
        return {"date": {"start": dt.isoformat()}}
    return {"date": None}

def already_logged(db_id, date_str):
    """Check if a record already exists in Notion."""
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Date", "date": {"equals": date_str}}
    )
    return len(results.get("results", [])) > 0

def push_to_notion(db_id, row, name="Item"):
    """Push a row to Notion if not already logged."""
    date_str = row["Date"]["date"]["start"]
    if not already_logged(db_id, date_str):
        notion.pages.create(parent={"database_id": db_id}, properties=row)
        print(f"✅ Added {name} for {date_str}")
    else:
        print(f"⚠️ {name} already logged for {date_str}")

# ----------------------
# Pull Garmin data
# ----------------------
today = date.today()
today_str = today.isoformat()

# Activities (latest only)
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    distance_mi = km_to_miles(act.get("distance", 0)/1000)
    pace_mi = min_per_km_to_min_per_mile(act.get("averageSpeed", 0) and 60/act["averageSpeed"] or 0)
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": distance_mi},
        "Duration (min)": {"number": round(act.get("duration",0)/60,1)},
        "Avg Pace (min/mi)": {"number": pace_mi},
    }

# Daily stats
daily_stats = garmin_client.get_stats(today_str)
# Steps
steps_list = garmin_client.get_daily_steps(today_str, today_str)
steps_count = steps_list[0].get("steps", 0) if steps_list else 0

# Body battery
body_battery = garmin_client.get_body_battery(today_str)
body_battery_value = 0
if body_battery:
    if isinstance(body_battery, list):
        latest = body_battery[-1]
        body_battery_value = latest.get("bodyBatteryValue", 0) if isinstance(latest, dict) else 0
    elif isinstance(body_battery, dict):
        body_battery_value = body_battery.get("bodyBatteryValue", 0)

# Training readiness & status
training_readiness = garmin_client.get_training_readiness(today_str)
training_status = garmin_client.get_training_status(today_str)

# Sleep
sleep_list = garmin_client.get_sleep_data(today_str)
sleep = sleep_list[0] if sleep_list else {}
bedtime = sleep.get("startTimeLocal") if sleep else None
wake_time = sleep.get("endTimeLocal") if sleep else None
sleep_score = sleep.get("sleepScore", 0) if sleep else 0

# Personal Records
pr_list = garmin_client.get_personal_record()
pr_data = {}
if pr_list:
    for pr in pr_list:
        if isinstance(pr, dict) and "name" in pr and "value" in pr:
            pr_data[pr["name"]] = pr["value"]

# ----------------------
# Construct Notion rows
# ----------------------
health_row = {
    "Date": {"date": {"start": today_str}},
    "Body Battery": {"number": body_battery_value},
    "Resting HR": {"number": daily_stats.get("restingHeartRate", 0)},
    "Stress": {"number": daily_stats.get("stress", 0)},
}

steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_count},
}

sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Bedtime": build_notion_date(bedtime),
    "Wake Time": build_notion_date(wake_time),
    "Sleep Score": {"number": sleep_score},
}

pr_row = {"Date": {"date": {"start": today_str}}}
for k, v in pr_data.items():
    pr_row[k] = {"number": v}

# ----------------------
# Push to Notion
# ----------------------
if activity_row:
    push_to_notion(NOTION_ACTIVITIES_DB_ID, activity_row, "Activity")
push_to_notion(NOTION_HEALTH_DB_ID, health_row, "Health")
push_to_notion(NOTION_STEPS_DB_ID, steps_row, "Steps")
push_to_notion(NOTION_SLEEP_DB_ID, sleep_row, "Sleep")
if pr_data:
    push_to_notion(NOTION_PR_DB_ID, pr_row, "Personal Records")
