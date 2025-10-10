import os
from datetime import datetime, date
from notion_client import Client
from garminconnect import Garmin, GarminConnectAuthenticationError

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
try:
    garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    garmin_client.login()  # legacy bypass works on GitHub workflow
except GarminConnectAuthenticationError as e:
    print("❌ Garmin login failed:", e)
    exit(1)

# ----------------------
# Helper functions
# ----------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def already_logged(db_id, date_str):
    """Return True if an entry already exists for a given date."""
    if not db_id:
        return False
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Date", "date": {"equals": date_str}}
    )
    return len(results.get("results", [])) > 0

def pr_already_logged(db_id, pr_type, value):
    """Return True if a PR already exists for the given type and value."""
    results = notion.databases.query(
        database_id=db_id,
        filter={
            "and": [
                {"property": "Type", "select": {"equals": pr_type}},
                {"property": "Value", "number": {"equals": value}}
            ]
        }
    )
    return len(results.get("results", [])) > 0

# ----------------------
# Get today's date
# ----------------------
today = date.today()
today_str = today.strftime("%Y-%m-%d")

# ----------------------
# 1. Pull Garmin data
# ----------------------

# Activities (latest)
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

# Daily stats: steps, sleep, body metrics
daily_summary = garmin_client.get_stats(today_str)

# Steps
steps = daily_summary.get("steps", 0)
# Sleep
sleep_score = daily_summary.get("sleepScore", 0)
# Body battery
body_battery = daily_summary.get("bodyBattery", 0)
# Weight (lbs)
weight_lb = daily_summary.get("weight", 0) * 2.20462

health_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps},
    "Sleep Score": {"number": sleep_score},
    "Body Battery": {"number": body_battery},
    "Bodyweight (lb)": {"number": weight_lb}
}

# Personal Records (PRs)
pr_data = garmin_client.get_personal_records()
pr_rows = []
if pr_data:
    for pr_type, value in pr_data.items():
        if value and not pr_already_logged(NOTION_PR_DB_ID, pr_type, value):
            pr_rows.append({
                "Type": {"select": {"name": pr_type}},
                "Value": {"number": value},
                "Date": {"date": {"start": today_str}}
            })

# ----------------------
# 2. Push to Notion
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
for row in pr_rows:
    notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=row)
    print(f"🏆 New PR logged: {row['Type']['select']['name']} = {row['Value']['number']}")


