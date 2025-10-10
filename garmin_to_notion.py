import os
from datetime import datetime, date
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
# Connect clients
# ----------------------
notion = Client(auth=NOTION_TOKEN)
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
    if dt:
        # Convert string or datetime to ISO format
        if isinstance(dt, str):
            return {"date": {"start": dt}}
        return {"date": {"start": dt.isoformat()}}
    return {"date": None}

def already_logged(db_id, date_str):
    try:
        results = notion.databases.query(
            database_id=db_id,
            filter={"property": "Date", "date": {"equals": date_str}}
        )
        return len(results.get("results", [])) > 0
    except Exception:
        return False

def push_to_notion(db_id, payload, label):
    if not already_logged(db_id, payload["Date"]["date"]["start"]):
        notion.pages.create(parent={"database_id": db_id}, properties=payload)
        print(f"✅ Added {label} for {payload['Date']['date']['start']}")
    else:
        print(f"⚠️ {label} already logged or missing")

today_str = datetime.now().strftime("%Y-%m-%d")

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
# Daily Stats (steps, sleep, body battery)
# ----------------------
daily_summary = garmin_client.get_stats(today_str)
steps_data = garmin_client.get_daily_steps(today_str)
body_battery_list = garmin_client.get_body_battery(today_str)

steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_data[0].get("steps", 0) if steps_data else 0},
}
push_to_notion(NOTION_STEPS_DB_ID, steps_row, "Steps")

body_battery = body_battery_list[0] if body_battery_list else {}
health_row = {
    "Date": {"date": {"start": today_str}},
    "Bodyweight (lb)": {"number": daily_summary.get("weight", 0) * 2.20462},
    "Body Battery": {"num
