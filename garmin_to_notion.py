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
    try:
        return {"number": float(value)}
    except Exception:
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

def extract_value(data, keys):
    if not data:
        return None
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                val = data[key]
                if isinstance(val, (int, float, str)):
                    return val
                nested = extract_value(val, keys)
                if nested is not None:
                    return nested
        for v in data.values():
            nested = extract_value(v, keys)
            if nested is not None:
                return nested
    elif isinstance(data, list):
        for item in data:
            nested = extract_value(item, keys)
            if nested is not None:
                return nested
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
# DEBUG RAW GARMIN DATA
# ---------------------------
logging.info("🔍 Raw Garmin data snapshot:")
logging.info("Body Battery:")
pprint.pprint(body_battery)
logging.info("Sleep Data:")
pprint.pprint(sleep_data)
logging.info("Training Status:")
pprint.pprint(status)

# ---------------------------
# PARSE HEALTH METRICS
# ---------------------------

# --- Sleep ---
sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
sleep_score = extract_value(sleep_daily, ["sleepScore", "overallScore", "overall", "unknown_0", "score"])
bed_time = sleep_daily.get("sleepStartTimestampGMT")
wake_time = sleep_daily.get("sleepEndTimestampGMT")

# --- Body Battery (Min / Max) ---
body_battery_min = None
body_battery_max = None
if isinstance(body_battery, list) and len(body_battery) > 0:
    bb = body_battery[0]
    arr = bb.get("bodyBatteryValuesArray") or []
    values = [v[1] for v in arr if isinstance(v, list) and len(v) > 1]
    if values:
        body_battery_min = min(values)
        body_battery_max = max(values)

# --- Body Weight ---
body_weight = None
if body_comp.get("dateWeightList"):
    w_raw = body_comp["dateWeightList"][0].get("weight")
    if w_raw:
        body_weight = round(float(w_raw) / 453.592, 2)

# --- Training Readiness ---
training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"])

# --- Training Status (map to Notion options) ---
training_status_map = {
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
}
training_status_val = extract_value(status, ["trainingStatus", "status", "unknown_2", "currentStatus"])
if isinstance(training_status_val, (int, float)):
    training_status_val = training_status_map.get(int(training_status_val), "Maintaining")

# Override if body battery feedback indicates recovery
feedback_hint = extract_value(body_battery, ["feedbackShortType", "feedbackLongType"])
if feedback_hint and "RECOVERING" in str(feedback_hint).upper():
    training_status_val = "Recovery"

# --- Stress / HR / Calories / Steps ---
stress = extract_value(stats, ["stressLevelAvg", "stressScore", "overallStressLevel", "stressLevel", "stress_level_value"])
resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"])
calories = extract_value(stats, ["totalKilocalories", "active_calories"])
steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

# ---------------------------
# DEBUG PARSED METRICS
# ---------------------------
logging.info("🧠 Garmin health metrics summary after parsing:")
logging.info(f"  Steps: {steps_total}")
logging.info(f"  Body Weight (lbs): {body_weight}")
logging.info(f"  Body Battery Min: {body_battery_min}")
logging.info(f"  Body Battery Max: {body_battery_max}")
logging.info(f"  Sleep Score: {sleep_score}")
logging.info(f"  Bedtime: {bed_time}")
logging.info(f"  Wake Time: {wake_time}")
logging.info(f"  Training Readiness: {training_readiness}")
logging.info(f"  Training Status: {training_status_val}")
logging.info(f"  Resting HR: {resting_hr}")
logging.info(f"  Stress: {stress}")
logging.info(f"  Calories Burned: {calories}")

# ---------------------------
# PUSH TO NOTION
# ---------------------------
health_props = {
    "Name": notion_title(yesterday_str),
    "Date": notion_date(yesterday_str),
    "Steps": notion_number(steps_total),
    "Body Weight": notion_number(body_weight),
    "Body Battery Min": notion_number(body_battery_min),
    "Body Battery Max": notion_number(body_battery_max),
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
# PUSH ACTIVITIES
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
