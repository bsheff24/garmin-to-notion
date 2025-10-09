# garmin_to_notion_unified.py
import os
from datetime import datetime, timedelta
from notion_client import Client
from garminconnect import Garmin

# --- Load environment variables ---
GARMIN_USERNAME = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")

# --- Initialize Notion client ---
notion = Client(auth=NOTION_TOKEN)

# --- Initialize Garmin client ---
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # Will use session token in GitHub Actions

# --- Define unit conversion helpers ---
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mi(min_per_km):
    return round(min_per_km / 0.621371, 2)

# --- Fetch Garmin data ---
today = datetime.now().date()
yesterday = today - timedelta(days=1)

# Activities
activities = garmin_client.get_activities(0, 10)  # last 10 activities
activity_rows = []
for act in activities:
    activity_rows.append({
        "Date": {"date": {"start": act["startTimeLocal"].split(" ")[0]}},
        "Type": {"select": {"name": act.get("activityType", {}).get("typeKey", "Unknown")}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},  # Garmin distance is in meters
        "Duration (min)": {"number": round(act.get("duration", 0)/60, 2)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mi(act.get("averageSpeed", 0)) if act.get("averageSpeed") else None},
        "Steps": {"number": act.get("steps", 0)}
    })

# Daily Health Metrics
daily_metrics = garmin_client.get_stats()  # includes weight, sleep, heart rate
health_row = {
    "Date": {"date": {"start": str(today)}},
    "Bodyweight (lb)": {"number": daily_metrics.get("weight", {}).get("weight", 0)},
    "Sleep Score": {"number": daily_metrics.get("sleep", {}).get("score", 0)},
    "Steps": {"number": daily_metrics.get("steps", 0)}
}

# --- Push to Notion ---
# Activities DB
for row in activity_rows:
    try:
        notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=row)
        print(f"Added activity on {row['Date']['date']['start']}")
    except Exception as e:
        print(f"Failed to add activity: {e}")

# Health Metrics DB
try:
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_row)
    print(f"Added health metrics for {health_row['Date']['date']['start']}")
except Exception as e:
    print(f"Failed to add health metrics: {e}")
