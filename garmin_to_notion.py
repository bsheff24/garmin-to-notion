import os
from datetime import datetime
from notion_client import Client
from garminconnect import Garmin

# ----------------------
# Environment variables
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
# Notion client
# ----------------------
notion = Client(auth=NOTION_TOKEN)

# ----------------------
# Garmin client
# ----------------------
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()

# ----------------------
# Helper functions
# ----------------------
def build_notion_date(dt_str):
    """Convert ISO string to Notion date object"""
    if dt_str:
        return {"date": {"start": dt_str}}
    return {"date": None}

def already_logged(db_id, date_str):
    """Check if an entry already exists for the date"""
    try:
        results = notion.databases.query(
            database_id=db_id,
            filter={"property": "Date", "date": {"equals": date_str}}
        )
        return len(results.get("results", [])) > 0
    except Exception as e:
        print(f"⚠️ Notion query error: {e}")
        return False

def push_to_notion(db_id, row, label):
    """Push row to Notion if not already logged"""
    date = row.get("Date", {}).get("date", {}).get("start")
    if date and not already_logged(db_id, date):
        notion.pages.create(parent={"database_id": db_id}, properties=row)
        print(f"✅ Added {label} for {date}")
    else:
        print(f"⚠️ {label} already logged or missing")

# ----------------------
# Today's date
# ----------------------
today_str = datetime.now().strftime("%Y-%m-%d")

# ----------------------
# Activities
# ----------------------
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    distance_mi = round(act.get("distance", 0)/1000 * 0.621371, 2)
    avg_speed = act.get("averageSpeed", 0)
    avg_pace = round(60/avg_speed/0.621371, 2) if avg_speed else 0
    activity_row = {
        "Date": build_notion_date(act.get("startTimeLocal")[:10]),
        "Distance (mi)": {"number": distance_mi},
        "Duration (min)": {"number": round(act.get("duration",0)/60, 1)},
        "Avg Pace (min/mi)": {"number": avg_pace}
    }
push_to_notion(NOTION_ACTIVITIES_DB_ID, activity_row, "Activity")

# ----------------------
# Daily steps
# ----------------------
steps_list = garmin_client.get_daily_steps(today_str, today_str)
steps = steps_list[0] if isinstance(steps_list, list) and steps_list else {}
steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps.get("steps", 0)},
}
push_to_notion(NOTION_STEPS_DB_ID, steps_row, "Steps")

# ----------------------
# Sleep data
# ----------------------
sleep_list = garmin_client.get_sleep_data(today_str)
sleep = sleep_list[0] if isinstance(sleep_list, list) and sleep_list else {}
sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Bedtime": build_notion_date(sleep.get("startTimeLocal")),
    "Wake Time": build_notion_date(sleep.get("endTimeLocal")),
    "Sleep Score": {"number": sleep.get("sleepScore", 0)},
}
push_to_notion(NOTION_SLEEP_DB_ID, sleep_row, "Sleep")

# ----------------------
# Body battery & health metrics
# ----------------------
body_battery_list = garmin_client.get_body_battery(today_str, today_str)
body_battery = body_battery_list[0] if isinstance(body_battery_list, list) and body_battery_list else {}
daily_summary = garmin_client.get_stats(today_str)

health_row = {
    "Date": {"date": {"start": today_str}},
    "Body Battery": {"number": body_battery.get("bodyBatteryValue", 0)},
    "Steps": {"number": daily_summary.get("steps", 0)},
    "Sleep Score": {"number": daily_summary.get("sleepScore", 0)},
    "Bodyweight (lb)": {"number": round(daily_summary.get("weight", 0) * 2.20462, 1)},
}
push_to_notion(NOTION_HEALTH_DB_ID, health_row, "Health Metrics")

# ----------------------
# Personal records
# ----------------------
pr_list = garmin_client.get_personal_record()
if isinstance(pr_list, list):
    for pr in pr_list:
        pr_type = pr.get("activityType", "Unknown")
        value = pr.get("recordValue", 0)
        date_pr = pr.get("startTimeLocal", today_str)[:10]
        pr_row = {
            "Date": {"date": {"start": date_pr}},
            "PR Type": {
                "rich_text": [
                    {"text": {"content": pr_type}}
                ]
            },
            "Value": {
                "rich_text": [
                    {"text": {"content": str(value)}}
                ]
            },
        }
        push_to_notion(NOTION_PR_DB_ID, pr_row, "Personal Record")

