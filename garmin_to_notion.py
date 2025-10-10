import os
import datetime
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")

# ---------------------------
# CLIENTS
# ---------------------------
notion = Client(auth=NOTION_TOKEN)
garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin.login()

# ---------------------------
# HELPERS
# ---------------------------
def notion_date(dt):
    if not dt:
        return {"date": None}
    if isinstance(dt, (int, float)):
        # Garmin timestamps are in milliseconds
        dt_obj = datetime.datetime.utcfromtimestamp(dt / 1000)
        return {"date": {"start": dt_obj.isoformat()}}
    if isinstance(dt, str):
        return {"date": {"start": dt}}
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    return {"number": float(value)} if value is not None else {"number": None}

def notion_text(value):
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": str(value)}}]}

def already_logged(db_id, date_str):
    response = notion.databases.query(
        **{
            "database_id": db_id,
            "filter": {"property": "Date", "date": {"equals": date_str}},
        }
    )
    return len(response.get("results", [])) > 0

def safe_extract_list(data_list, key, date_key=None, date_val=None):
    """Extract a key from a list of dicts, optionally filtering by date."""
    if not data_list:
        return None
    if isinstance(data_list, list):
        if date_key and date_val:
            for item in data_list:
                if item.get(date_key) == date_val:
                    return item.get(key)
            return None
        else:
            return data_list[0].get(key)
    if isinstance(data_list, dict):
        return data_list.get(key)
    return None

# ---------------------------
# DATE SETUP
# ---------------------------
today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.isoformat()
print(f"📅 Collecting Garmin data for {yesterday_str}")

# ---------------------------
# FETCH DATA FROM GARMIN
# ---------------------------
try:
    activities = garmin.get_activities(0, 10)
except Exception as e:
    print("⚠️ Failed to fetch activities:", e)
    activities = []

try:
    steps_data = garmin.get_daily_steps(yesterday_str, yesterday_str)
except Exception as e:
    print("⚠️ Steps unavailable:", e)
    steps_data = []

try:
    sleep_response = garmin.get_sleep_data(yesterday_str)
except Exception as e:
    print("⚠️ Sleep data unavailable:", e)
    sleep_response = {}

try:
    body_battery = garmin.get_body_battery(yesterday_str, yesterday_str)
except Exception as e:
    print("⚠️ Body Battery unavailable:", e)
    body_battery = []

try:
    body_comp = garmin.get_body_composition(yesterday_str)
except Exception as e:
    print("⚠️ Body composition unavailable:", e)
    body_comp = []

try:
    training_readiness = garmin.get_training_readiness(yesterday_str)
except Exception as e:
    print("⚠️ Training readiness unavailable:", e)
    training_readiness = []

try:
    training_status = garmin.get_training_status(yesterday_str)
except Exception as e:
    print("⚠️ Training status unavailable:", e)
    training_status = []

try:
    stats = garmin.get_stats_and_body(yesterday_str)
except Exception as e:
    print("⚠️ Stats unavailable:", e)
    stats = {}

# ---------------------------
# PARSE HEALTH METRICS
# ---------------------------
# Steps
steps_total = 0
if isinstance(steps_data, list) and len(steps_data) > 0:
    steps_total = sum(item.get("totalSteps", 0) for item in steps_data)

# Body weight (grams → kg)
weight_grams = safe_extract_list(body_comp, "weight")
weight_kg = weight_grams / 1000 if weight_grams else None

# Sleep
sleep_data = sleep_response.get("dailySleepDTO", {})
sleep_score = safe_extract_list(sleep_data.get("sleepScores", {}).get("overall", {}), "value")
bed_time = sleep_data.get("sleepStartTimestampGMT")
wake_time = sleep_data.get("sleepEndTimestampGMT")

# Body battery
body_battery_value = safe_extract_list(body_battery, "bodyBatteryValue")

# Training readiness
training_readiness_score = safe_extract_list(training_readiness, "score", "calendarDate", yesterday_str)
# Training status
status_list = safe_extract_list(training_status, "latestTrainingStatusData")
training_status_val = None
if status_list:
    ts_data = status_list.get(next(iter(status_list)), {})
    if ts_data.get("calendarDate") == yesterday_str:
        training_status_val = ts_data.get("trainingStatus")

# Other stats
resting_hr = safe_extract_list(stats, "restingHeartRate")
stress = safe_extract_list(stats, "stressLevelAvg")
calories = safe_extract_list(stats, "totalKilocalories")

# ---------------------------
# PUSH TO NOTION - HEALTH METRICS
# ---------------------------
if not already_logged(NOTION_HEALTH_DB_ID, yesterday_str):
    try:
        properties = {
            "Date": notion_date(yesterday_str),
            "Steps": notion_number(steps_total if steps_total > 0 else None),
            "Body Weight": notion_number(weight_kg),
            "Body Battery": notion_number(body_battery_value),
            "Sleep Score": notion_number(sleep_score),
            "Bedtime": notion_date(bed_time),
            "Wake Time": notion_date(wake_time),
            "Training Readiness": notion_number(training_readiness_score),
            "Training Status": notion_text(training_status_val),
            "Resting HR": notion_number(resting_hr),
            "Stress": notion_number(stress),
            "Calories Burned": notion_number(calories),
        }

        notion.pages.create(
            parent={"database_id": NOTION_HEALTH_DB_ID},
            properties=properties,
        )
        print(f"✅ Added Health Metrics for {yesterday_str}")
    except Exception as e:
        print("⚠️ Failed to push health metrics:", e)
else:
    print("ℹ️ Health Metrics already logged for", yesterday_str)

# ---------------------------
# PUSH TO NOTION - ACTIVITIES
# ---------------------------
for act in activities:
    try:
        act_date = act.get("startTimeLocal", "")[:10]
        if already_logged(NOTION_ACTIVITIES_DB_ID, act_date):
            continue

        props = {
            "Date": notion_date(act_date),
            "Activity Name": notion_text(act.get("activityName")),
            "Distance (km)": notion_number(act.get("distance") / 1000 if act.get("distance") else None),
            "Calories": notion_number(act.get("calories")),
            "Duration (min)": notion_number(round(act.get("duration") / 60, 1) if act.get("duration") else None),
            "Type": notion_text(act.get("activityType", {}).get("typeKey")),
        }

        notion.pages.create(
            parent={"database_id": NOTION_ACTIVITIES_DB_ID},
            properties=props,
        )
        print(f"✅ Logged activity: {act.get('activityName')}")
    except Exception as e:
        print(f"⚠️ Failed to log activity: {e}")

garmin.logout()
print("🏁 Sync complete.")
