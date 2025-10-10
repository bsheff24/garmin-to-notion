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
# Initialize clients
# ----------------------
notion = Client(auth=NOTION_TOKEN)
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # Legacy bypass works on GitHub workflow

# ----------------------
# Helpers
# ----------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def build_notion_date(dt_str):
    if dt_str:
        return {"date": {"start": dt_str}}
    return {"date": None}

def already_logged(db_id, date_str):
    try:
        results = notion.databases.query(
            database_id=db_id,
            filter={"property": "Date", "date": {"equals": date_str}}
        )
        return len(results.get("results", [])) > 0
    except:
        return False  # Fail safe if database not found

def push_to_notion(db_id, row, label="Data"):
    if row and not already_logged(db_id, row["Date"]["date"]["start"]):
        notion.pages.create(parent={"database_id": db_id}, properties=row)
        print(f"✅ Added {label} for {row['Date']['date']['start']}")
    else:
        print(f"⚠️ {label} already logged or missing")

# ----------------------
# Dates
# ----------------------
today = date.today()
today_str = today.strftime("%Y-%m-%d")

# ----------------------
# Activities
# ----------------------
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
push_to_notion(NOTION_ACTIVITIES_DB_ID, activity_row, "Activity")

# ----------------------
# Health Metrics / Steps / Body Battery
# ----------------------
daily_summary = garmin_client.get_stats_and_body(today_str)
steps = daily_summary.get("steps", 0)
body_battery_value = daily_summary.get("bodyBatteryValue", 0)
weight = daily_summary.get("weight", 0) or 0

health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps},
    "Body Battery": {"number": body_battery_value},
    "Bodyweight (lb)": {"number": weight * 2.20462}
}
push_to_notion(NOTION_HEALTH_DB_ID, health_row, "Health Metrics")
push_to_notion(NOTION_STEPS_DB_ID, {"Date": {"date": {"start": today_str}}, "Steps": {"number": steps}}, "Steps")

# ----------------------
# Sleep
# ----------------------
sleep_list = garmin_client.get_sleep_data(today_str, today_str)
if isinstance(sleep_list, list) and sleep_list:
    sleep = sleep_list[0]
elif isinstance(sleep_list, dict):
    sleep = sleep_list
else:
    sleep = {}

bedtime = sleep.get("startTimeLocal")
wake_time = sleep.get("endTimeLocal")
sleep_score = sleep.get("sleepScore", 0)

sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Bedtime": build_notion_date(bedtime),
    "Wake Time": build_notion_date(wake_time),
    "Sleep Score": {"number": sleep_score},
}
push_to_notion(NOTION_SLEEP_DB_ID, sleep_row, "Sleep")

# ----------------------
# Personal Records
# ----------------------
pr_list = garmin_client.get_personal_record()
pr_row = {"Date": {"date": {"start": today_str}}}
if pr_list and isinstance(pr_list, list):
    for pr in pr_list:
        pr_type = pr.get("name")
        pr_value = pr.get("value")
        if pr_type and pr_value is not None:
            pr_row[pr_type] = {"number": pr_value}
push_to_notion(NOTION_PR_DB_ID, pr_row, "Personal Records")

