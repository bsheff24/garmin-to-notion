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
# Connect to APIs
# ----------------------
notion = Client(auth=NOTION_TOKEN)
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # works in GitHub workflow

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

# ----------------------
# Today's date
# ----------------------
today_str = date.today().strftime("%Y-%m-%d")

# ----------------------
# 1. Pull Garmin data
# ----------------------
# Activities
activities = garmin_client.get_activities(1)  # latest activity
activity_row = {}
if activities:
    act = activities[0]
    distance_miles = km_to_miles(act.get("distance", 0)/1000)
    duration_min = round(act.get("duration",0)/60,1)
    avg_speed = act.get("averageSpeed",0)
    pace_min_per_mile = min_per_km_to_min_per_mile(60/avg_speed) if avg_speed else 0

    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": distance_miles},
        "Duration (min)": {"number": duration_min},
        "Avg Pace (min/mi)": {"number": pace_min_per_mile}
    }

# Daily summary metrics
daily_summary = garmin_client.get_stats(today_str)  # returns dict of steps, sleepScore, weight, etc.

health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": daily_summary.get("steps",0)},
    "Sleep Score": {"number": daily_summary.get("sleepScore",0)},
    "Bodyweight (lb)": {"number": daily_summary.get("weight",0)*2.20462},
    "Body Battery": {"number": daily_summary.get("bodyBattery",0)},
    "Training Readiness": {"number": daily_summary.get("trainingReadiness",0)},
    "Training Status": {"number": daily_summary.get("trainingStatus",0)}
}

# Sleep metrics (if you want separate DB)
sleep_data = garmin_client.get_sleep(today_str)
sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Bed Time": {"date": {"start": sleep_data.get("bedTimeStart")}},
    "Wake Time": {"date": {"start": sleep_data.get("bedTimeEnd")}},
    "Sleep Score": {"number": sleep_data.get("sleepScore",0)}
}

# Personal records
pr_data = garmin_client.get_personal_record()  # returns a list
pr_rows = []
for record in pr_data:
    pr_rows.append({
        "Date": {"date": {"start": today_str}},
        "PR Type": {"title":[{"text":{"content": record.get("type")}}]},
        "Value": {"number": record.get("value")}
    })

# ----------------------
# 2. Push to Notion
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

if sleep_row and not already_logged(NOTION_SLEEP_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_SLEEP_DB_ID}, properties=sleep_row)
    print(f"✅ Sleep metrics added for {today_str}")
else:
    print(f"⚠️ Sleep metrics already logged or missing")

for pr_row in pr_rows:
    if not already_logged(NOTION_PR_DB_ID, pr_row["Date"]["date"]["start"]):
        notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=pr_row)
        print(f"✅ Personal record added: {pr_row['PR Type']['title'][0]['text']['content']}")
    else:
        print(f"⚠️ Personal record already logged: {pr_row['PR Type']['title'][0]['text']['content']}")



