import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------
# CLIENTS
# ---------------------------
logging.info("🔐 Logging into Garmin...")
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
        dt = datetime.datetime.fromtimestamp(dt / 1000)
    if isinstance(dt, datetime.datetime):
        dt = dt.isoformat()
    return {"date": {"start": str(dt)}}

def notion_number(value):
    if value is None:
        return {"number": None}
    if isinstance(value, (int, float, str)):
        try:
            return {"number": float(value)}
        except Exception:
            return {"number": None}
    return {"number": None}

def notion_select(value):
    if not value:
        return {"select": None}
    return {"select": {"name": str(value)}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value)}}]}

def safe_fetch(func, *args):
    try:
        return func(*args)
    except Exception as e:
        logging.warning(f"⚠️ {func.__name__} unavailable: {e}")
        return None

def extract_numeric(data, keys):
    if not data:
        return None
    if isinstance(data, dict):
        for key in keys:
            val = data.get(key)
            if isinstance(val, (int, float)):
                return val
            if isinstance(val, (dict, list)):
                nested = extract_numeric(val, keys)
                if nested is not None:
                    return nested
    if isinstance(data, list):
        for item in data:
            nested = extract_numeric(item, keys)
            if nested is not None:
                return nested
    if isinstance(data, (int, float)):
        return data
    return None

def extract_string(data, keys):
    if not data:
        return None
    if isinstance(data, dict):
        for key in keys:
            val = data.get(key)
            if isinstance(val, str):
                return val
            if isinstance(val, (dict, list)):
                nested = extract_string(val, keys)
                if nested is not None:
                    return nested
    if isinstance(data, list):
        for item in data:
            nested = extract_string(item, keys)
            if nested is not None:
                return nested
    if isinstance(data, str):
        return data
    return None

# ---------------------------
# DATE SETUP
# ---------------------------
today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.isoformat()
logging.info(f"📅 Collecting Garmin data for {yesterday_str}")

# ---------------------------
# FETCH GARMIN DATA
# ---------------------------
activities = safe_fetch(garmin.get_activities, 0, 10) or []
steps = safe_fetch(garmin.get_daily_steps, yesterday_str, yesterday_str) or []
sleep_data = safe_fetch(garmin.get_sleep_data, yesterday_str) or {}
body_battery = safe_fetch(garmin.get_body_battery, yesterday_str, yesterday_str) or []
body_comp = safe_fetch(garmin.get_body_composition, yesterday_str) or {}
readiness = safe_fetch(garmin.get_training_readiness, yesterday_str) or []
status = safe_fetch(garmin.get_training_status, yesterday_str) or {}
stats = safe_fetch(garmin.get_stats_and_body, yesterday_str) or {}

# ---------------------------
# DEBUG: dump raw Garmin responses
# ---------------------------
logging.info("🔍 Raw Garmin data debug dump:")
logging.info("Body Battery:")
pprint.pprint(body_battery)
logging.info("Sleep Data:")
pprint.pprint(sleep_data)
logging.info("Stats Data:")
pprint.pprint(stats)
logging.info("Training Status:")
pprint.pprint(status)

# ---------------------------
# PARSE HEALTH METRICS
# ---------------------------
sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
sleep_score = extract_numeric(sleep_daily, ["sleepScore", "overall", "overallScore"])
bed_time = sleep_daily.get("sleepStartTimestampGMT")
wake_time = sleep_daily.get("sleepEndTimestampGMT")

body_battery_value = extract_numeric(body_battery, ["bodyBatteryValue", "value", "bodyBatteryHighestValue"])

body_weight = None
if body_comp.get("dateWeightList"):
    w_raw = body_comp["dateWeightList"][0].get("weight")
    if w_raw:
        body_weight = round(float(w_raw) / 453.592, 2)  # grams → lbs

training_readiness = extract_numeric(readiness, ["score"])

# NEW: robust Training Status extraction
training_status_val = extract_string(status, ["trainingStatus", "status"])
if not training_status_val and isinstance(status, dict) and "summary" in status:
    training_status_val = extract_string(status["summary"], ["trainingStatus", "status"])
if not training_status_val:
    training_status_val = "UNKNOWN"

resting_hr = extract_numeric(stats, ["restingHeartRate"])
stress = extract_numeric(stats, ["stressLevelAvg", "stressScore", "overallStressLevel", "stressLevel"])
calories = extract_numeric(stats, ["totalKilocalories"])

steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

# ---------------------------
# DEBUG HEALTH METRICS
# ---------------------------
logging.info("🧠 Garmin health metrics summary after parsing:")
logging.info(f"  Steps: {steps_total}")
logging.info(f"  Body Weight (lbs): {body_weight}")
logging.info(f"  Body Battery: {body_battery_value}")
logging.info(f"  Sleep Score: {sleep_score}")
logging.info(f"  Training Readiness: {training_readiness}")
logging.info(f"  Training Status: {training_status_val}")
logging.info(f"  Resting HR: {resting_hr}")
logging.info(f"  Stress: {stress}")
logging.info(f"  Calories Burned: {calories}")

# ---------------------------
# PUSH HEALTH METRICS TO NOTION
# ---------------------------
health_props = {
    "Name": notion_title(yesterday_str),
    "Date": notion_date(yesterday_str),
    "Steps": notion_number(steps_total),
    "Body Weight": notion_number(body_weight),
    "Body Battery": notion_number(body_battery_value),
    "Sleep Score": notion_number(sleep_score),
    "Bedtime": notion_date(bed_time),
    "Wake Time": notion_date(wake_time),
    "Training Readiness": notion_number(training_readiness),
    "Training Status": notion_select(training_status_val),
    "Resting HR": notion_number(resting_hr),
    "Stress": notion_number(stress),
    "Calories Burned": notion_number(calories),
}

logging.info("📤 Pushing Garmin health metrics to Notion...")
try:
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
    logging.info(f"✅ Synced health metrics for {yesterday_str}")
except Exception as e:
    logging.error(f"⚠️ Failed to push health metrics: {e}")

# ---------------------------
# PUSH ACTIVITIES TO NOTION
# ---------------------------
logging.info(f"📤 Syncing {len(activities)} activities...")

for act in activities:
    act_date = act.get("startTimeLocal", "")[:10] or yesterday_str
    activity_props = {
        "Date": notion_date(act_date),
        "Name": notion_title(act.get("activityName") or f"Activity {act_date}"),
        "Distance (km)": notion_number((act.get("distance") or 0) / 1000),
        "Calories": notion_number(act.get("calories")),
        "Duration (min)": notion_number(round((act.get("duration") or 0) / 60, 1)),
        "Type": notion_select(act.get("activityType", {}).get("typeKey")),
    }
    try:
        notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
        logging.info(f"🏃 Logged activity: {act.get('activityName')}")
    except Exception as e:
        logging.error(f"⚠️ Failed to log activity {act.get('activityName')}: {e}")

garmin.logout()
logging.info("🏁 Sync complete.")
