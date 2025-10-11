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
    # dt may be:
    # - ISO string -> return as-is
    # - integer ms since epoch -> convert
    # - datetime -> iso
    try:
        if isinstance(dt, str):
            # try to parse ISO-like strings; Notion accepts ISO strings
            return {"date": {"start": dt}}
        if isinstance(dt, (int, float)):
            # milliseconds -> seconds
            dt_obj = datetime.datetime.fromtimestamp(float(dt) / 1000)
            return {"date": {"start": dt_obj.isoformat()}}
        if isinstance(dt, datetime.datetime):
            return {"date": {"start": dt.isoformat()}}
    except Exception:
        pass
    return {"date": None}

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
        logging.debug(f"⚠️ {func.__name__} unavailable: {e}")
        return None

def extract_value(data, keys):
    """Recursively search for first occurrence of any of keys (strings)."""
    if data is None:
        return None
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                v = data[k]
                if isinstance(v, (int, float, str)):
                    return v
                # if nested, continue searching inside it
                nested = extract_value(v, keys)
                if nested is not None:
                    return nested
        # dive deeper through values
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
# FETCH GARMIN DATA (primary endpoints)
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
# Attempt wellness fallback endpoints (some accounts expose different names)
# ---------------------------
wellness = None
for method_name in ("get_wellness", "get_wellness_data", "get_daily_wellness"):
    wellness = safe_fetch(getattr(garmin, method_name, lambda *a, **k: None), yesterday_str)
    if wellness:
        break

# ---------------------------
# RAW DEBUG DUMPS (paste these blocks if things still fail)
# ---------------------------
logging.info("\n🔍 Raw Garmin debug blocks (copy these if we still need tweaks):\n")
logging.info("Body Battery:")
pprint.pprint(body_battery)
logging.info("\nSleep Data:")
pprint.pprint(sleep_data)
logging.info("\nTraining Status:")
pprint.pprint(status)
logging.info("\nWellness (fallback):")
pprint.pprint(wellness)
logging.info("\nStats & Body:")
pprint.pprint(stats)
logging.info("\nReadiness:")
pprint.pprint(readiness)

# ---------------------------
# PARSE HEALTH METRICS
# ---------------------------
# Sleep (try multiple shapes)
sleep_daily = sleep_data.get("dailySleepDTO", {}) if isinstance(sleep_data, dict) else {}
sleep_score = extract_value(sleep_daily, ["sleepScore", "overallScore", "overall", "score", "unknown_0"])
# fallback: check top-level sleep_data for any score-like key
if not sleep_score:
    sleep_score = extract_value(sleep_data, ["sleepScore", "overallScore", "overall", "score"])

# Bed/wake: prefer ISO strings if present; else handle epoch ms ints
def parse_possible_ts(val):
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float)):
        # treat as ms
        try:
            return datetime.datetime.fromtimestamp(float(val) / 1000).isoformat()
        except Exception:
            return None
    return None

bed_time = parse_possible_ts(sleep_daily.get("sleepStartTimestampGMT") or sleep_daily.get("sleepStartTimestampLocal") or sleep_daily.get("startTimestampGMT") or sleep_daily.get("startTimestampLocal"))
wake_time = parse_possible_ts(sleep_daily.get("sleepEndTimestampGMT") or sleep_daily.get("sleepEndTimestampLocal") or sleep_daily.get("endTimestampGMT") or sleep_daily.get("endTimestampLocal"))

# Body battery: numeric series + textual labels
body_battery_value = None
body_battery_high = None
body_battery_low = None
body_battery_text = None
if isinstance(body_battery, list) and len(body_battery) > 0:
    first_bb = body_battery[0]
    # numeric series under bodyBatteryValuesArray (list of [ts, val])
    arr = first_bb.get("bodyBatteryValuesArray") or first_bb.get("bodyBatteryValues") or []
    try:
        numeric_vals = [int(item[1]) for item in arr if isinstance(item, (list, tuple)) and len(item) >= 2]
        if numeric_vals:
            body_battery_value = numeric_vals[-1]   # last (most recent)
            body_battery_high = max(numeric_vals)
            body_battery_low = min(numeric_vals)
    except Exception:
        pass
    # textual labels
    body_battery_text = extract_value(first_bb, ["bodyBatteryLevel", "bodyBatteryDynamicFeedbackEvent", "endOfDayBodyBatteryDynamicFeedbackEvent"])
    # also try to extract specific fields if present
    if not body_battery_text:
        body_battery_text = extract_value(first_bb, ["feedbackShortType", "feedbackLongType"])
else:
    # if not list, try to extract directly
    body_battery_value = extract_value(body_battery, ["bodyBatteryValue", "value", "unknown_0"])
    body_battery_text = extract_value(body_battery, ["bodyBatteryLevel"])

# Body weight
body_weight = None
if isinstance(body_comp, dict) and body_comp.get("dateWeightList"):
    w_raw = body_comp["dateWeightList"][0].get("weight")
    if w_raw:
        try:
            body_weight = round(float(w_raw) / 453.592, 2)
        except Exception:
            body_weight = None

# Training readiness & status
training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"])

training_status_val = extract_value(status, ["trainingStatus", "status", "unknown_2", "currentStatus", "display"])
# If numeric code, map to friendly name (fallback mapping)
status_map = {
    0: "No Status",
    1: "Detraining",
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
    6: "Overreaching",
    7: "Unknown"
}
try:
    if isinstance(training_status_val, (int, float, str)) and str(training_status_val).isdigit():
        training_status_val = status_map.get(int(str(training_status_val)), training_status_val)
except Exception:
    pass

# Override heuristics: use endOfDay feedback hint if it contains RECOVER
try:
    fb1 = None
    if isinstance(body_battery, list) and body_battery:
        fb1 = (body_battery[0].get("endOfDayBodyBatteryDynamicFeedbackEvent") or {}).get("feedbackShortType") or (body_battery[0].get("bodyBatteryDynamicFeedbackEvent") or {}).get("feedbackShortType")
    fb2 = extract_value(body_battery, ["feedbackShortType", "feedbackLongType", "feedbackShort"])
    for fb in (fb1, fb2):
        if fb and isinstance(fb, str) and "RECOVER" in fb.upper():
            training_status_val = "Recovery"
            break
except Exception:
    pass

# Stress: try stats -> wellness fallback objects
stress = extract_value(stats, ["stressLevelAvg", "stressScore", "overallStressLevel", "stressLevel", "stress_level_value"])
if stress is None and wellness:
    stress = extract_value(wellness, ["stress_level_value", "stressLevel", "stress_score", "unknown_0"])

# HR / calories / steps
resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate", "resting_hr"])
calories = extract_value(stats, ["totalKilocalories", "active_calories", "calories"])
steps_total = 0
if isinstance(steps, list):
    steps_total = sum(i.get("totalSteps", 0) for i in steps)

# ---------------------------
# DEBUG PARSED METRICS
# ---------------------------
logging.info("\n🧠 Garmin parsed metrics (what we'll push):")
logging.info(f"  Steps: {steps_total}")
logging.info(f"  Body Weight (lbs): {body_weight}")
logging.info(f"  Body Battery (last): {body_battery_value}")
logging.info(f"  Body Battery High: {body_battery_high}")
logging.info(f"  Body Battery Low: {body_battery_low}")
logging.info(f"  Body Battery Text: {body_battery_text}")
logging.info(f"  Sleep Score: {sleep_score}")
logging.info(f"  Bedtime: {bed_time}")
logging.info(f"  Wake Time: {wake_time}")
logging.info(f"  Training Readiness: {training_readiness}")
logging.info(f"  Training Status: {training_status_val}")
logging.info(f"  Resting HR: {resting_hr}")
logging.info(f"  Stress: {stress}")
logging.info(f"  Calories Burned: {calories}")

# ---------------------------
# BUILD NOTION PAYLOAD
# ---------------------------
health_props = {
    "Name": notion_title(yesterday_str),
    "Date": notion_date(bed_time or yesterday_str),
    "Steps": notion_number(steps_total),
    "Body Weight": notion_number(body_weight),
    "Body Battery": notion_number(body_battery_value),
    "Body Battery High": notion_number(body_battery_high),
    "Body Battery Low": notion_number(body_battery_low),
    "Body Battery Level": notion_select(body_battery_text),
    "Sleep Score": notion_number(sleep_score),
    "Bedtime": notion_date(bed_time),
    "Wake Time": notion_date(wake_time),
    "Training Readiness": notion_number(training_readiness),
    "Training Status": notion_select(training_status_val),
    "Resting HR": notion_number(resting_hr),
    "Stress": notion_number(stress),
    "Calories Burned": notion_number(calories),
}

logging.info("\n📤 Pushing Garmin health metrics to Notion...")
try:
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
    logging.info(f"✅ Synced health metrics for {yesterday_str}")
except Exception as e:
    logging.error(f"⚠️ Failed to push health metrics: {e}")

# ---------------------------
# Activities (unchanged)
# ---------------------------
logging.info(f"\n📤 Syncing {len(activities)} activities...")
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
logging.info("\n🏁 Sync complete.")
