import os
import datetime
import logging
from garminconnect import Garmin
from notion_client import Client as NotionClient

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
notion = NotionClient(auth=NOTION_TOKEN)
garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin.login()

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
activities = garmin.get_activities(0, 10) or []
steps_data = garmin.get_daily_steps(yesterday_str, yesterday_str) or []
sleep_data = garmin.get_sleep_data(yesterday_str) or {}
body_battery_data = garmin.get_body_battery(yesterday_str, yesterday_str) or []
body_comp_data = garmin.get_body_composition(yesterday_str) or {}
training_readiness_data = garmin.get_training_readiness(yesterday_str) or []
training_status_data = garmin.get_training_status(yesterday_str) or {}
stats_data = garmin.get_stats_and_body(yesterday_str) or {}

# ---------------------------
# PARSE HEALTH METRICS (BASELINE WORKING LOGIC)
# ---------------------------
steps_total = sum(i.get("totalSteps", 0) for i in steps_data) if steps_data else 0

body_weight = None
if body_comp_data.get("dateWeightList"):
    w_raw = body_comp_data["dateWeightList"][0].get("weight")
    if w_raw:
        body_weight = round(float(w_raw) / 453.592, 2)

# Body Battery Min/Max
bb_min, bb_max = None, None
if isinstance(body_battery_data, list) and len(body_battery_data) > 0:
    bb = body_battery_data[0]
    values = bb.get("bodyBatteryValuesArray", [])
    if values and all(isinstance(v, list) and len(v) > 1 for v in values):
        numbers = [v[1] for v in values]
        bb_min = min(numbers)
        bb_max = max(numbers)

# Sleep (from baseline working script)
sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
sleep_score = sleep_daily.get("sleepScore") or sleep_daily.get("overallScore") or 0
bed_time = sleep_daily.get("sleepStartTimestampGMT")
wake_time = sleep_daily.get("sleepEndTimestampGMT")
bed_dt = datetime.datetime.fromtimestamp(bed_time / 1000) if bed_time else None
wake_dt = datetime.datetime.fromtimestamp(wake_time / 1000) if wake_time else None

# Training Readiness
training_readiness = training_readiness_data.get("score") if isinstance(training_readiness_data, dict) else 0

# Stats
resting_hr = stats_data.get("restingHeartRate") or 0
calories = stats_data.get("totalKilocalories") or 0

# ---------------------------
# FIX TRAINING STATUS
# ---------------------------
status_map = {
    0: "No Status",
    1: "Detraining",
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
    6: "Overreaching",
    7: "Unproductive",
    8: "Strained",
}

training_status_val = "Maintaining"

# numeric code mapping
code = training_status_data.get("trainingStatus") if training_status_data else None
if code is not None:
    training_status_val = status_map.get(int(code), "Maintaining")

# feedback text override
feedback = None
if training_status_data:
    feedback = training_status_data.get("feedbackShortType") or training_status_data.get("feedbackLongType")
if feedback and "RECOVERING" in feedback.upper():
    training_status_val = "Recovery"

# ---------------------------
# NOTION HELPERS
# ---------------------------
def notion_number(value):
    return {"number": float(value) if value is not None else None}

def notion_select(value):
    return {"select": {"name": str(value)}} if value else {"select": None}

def notion_date(dt):
    if not dt:
        return {"date": None}
    if isinstance(dt, (int, float)):
        dt = datetime.datetime.fromtimestamp(dt / 1000)
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
            except Exception:
                return {"date": None}
    return {"date": {"start": dt.isoformat()}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value)}}]}

# ---------------------------
# PUSH HEALTH METRICS
# ---------------------------
health_props = {
    "Name": notion_title(yesterday.strftime("%m/%d/%Y")),
    "Date": notion_date(yesterday_str),
    "Steps": notion_number(steps_total),
    "Body Weight": notion_number(body_weight),
    "Body Battery (Min)": notion_number(bb_min),
    "Body Battery (Max)": notion_number(bb_max),
    "Sleep Score": notion_number(sleep_score),
    "Bedtime": notion_date(bed_dt),
    "Wake Time": notion_date(wake_dt),
    "Training Readiness": notion_number(training_readiness),
    "Training Status": notion_select(training_status_val),
    "Resting HR": notion_number(resting_hr),
    "Calories Burned": notion_number(calories),
}

logging.info("📤 Pushing Garmin health metrics to Notion...")
try:
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
    logging.info(f"✅ Synced health metrics for {yesterday_str}")
except Exception as e:
    logging.error(f"❌ Failed to push health metrics: {e}")

# ---------------------------
# FIXED ACTIVITY LOGGING
# ---------------------------
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

