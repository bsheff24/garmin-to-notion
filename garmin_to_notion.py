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
garmin_client.login()  # Should work in GitHub Actions workflow

# ----------------------
# Helpers
# ----------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def already_logged(db_id, date_str):
    try:
        results = notion.databases.query(
            database_id=db_id,
            filter={"property": "Date", "date": {"equals": date_str}}
        )
        return len(results.get("results", [])) > 0
    except Exception as e:
        print(f"Error checking duplicates: {e}")
        return False

# ----------------------
# Pull Garmin Data
# ----------------------
today_str = date.today().strftime("%Y-%m-%d")

# 1. Latest Activity
activity_row = {}
activities = garmin_client.get_activities(1)
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal", "")[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},
        "Duration (min)": {"number": round(act.get("duration",0)/60,1)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mile(act.get("averageSpeed",0) and 60/act["averageSpeed"] or 0)}
    }

# 2. Daily Steps
steps_list = garmin_client.get_daily_steps(today_str, today_str)
steps_data = steps_list[0] if steps_list else {}
steps = steps_data.get("steps", 0)

# 3. Sleep
sleep_list = garmin_client.get_sleep_data(today_str, today_str)
sleep_data = sleep_list[0] if sleep_list else {}
sleep_score = sleep_data.get("sleepScore", 0)
bed_time = sleep_data.get("bedTime", None)
wake_time = sleep_data.get("wakeTime", None)

# 4. Body Metrics
body_battery = garmin_client.get_body_battery(today_str, today_str)[0].get("bodyBattery", 0) if garmin_client.get_body_battery(today_str, today_str) else 0
weight = garmin_client.get_body_composition(today_str, today_str)
bodyweight_lb = weight[0].get("weight", 0)*2.20462 if weight else 0

# 5. Training Readiness / Status
training_readiness_list = garmin_client.get_training_readiness(today_str, today_str)
training_readiness = training_readiness_list[0].get("readinessScore", 0) if training_readiness_list else 0
training_status = training_readiness_list[0].get("status", "") if training_readiness_list else ""

# 6. Personal Records
pr_list = garmin_client.get_personal_record()
pr_data = {pr["displayName"]: pr.get("value", 0) for pr in pr_list} if pr_list else {}

# ----------------------
# Build Health Row
# ----------------------
health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps},
    "Sleep Score": {"number": sleep_score},
    "Bedtime": {"date": {"start": bed_time}} if bed_time else {},
    "Wake Time": {"date": {"start": wake_time}} if wake_time else {},
    "Body Battery": {"number": body_battery},
    "Bodyweight (lb)": {"number": bodyweight_lb},
    "Training Readiness": {"number": training_readiness},
    "Training Status": {"rich_text": [{"text": {"content": training_status}}]}
}

# ----------------------
# Push to Notion
# ----------------------
if activity_row and not already_logged(NOTION_ACTIVITIES_DB_ID, activity_row["Date"]["date"]["start"]):
    notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_row)
    print(f"✅ Activity added for {activity_row['Date']['date']['start']}")
else:
    print(f"⚠️ Activity already logged or missing")

if health_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_row)
    print(f"✅ Health metrics added for {today_str}")
else:
    print(f"⚠️ Health metrics already logged or missing")

# ----------------------
# Push Personal Records
# ----------------------
for name, value in pr_data.items():
    pr_row = {
        "Date": {"date": {"start": today_str}},
        "Record Name": {"rich_text": [{"text": {"content": name}}]},
        "Value": {"number": value}
    }
    if not already_logged(NOTION_PR_DB_ID, today_str):
        notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=pr_row)
        print(f"✅ PR added: {name} = {value}")

