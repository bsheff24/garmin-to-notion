import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client
from zoneinfo import ZoneInfo  # Python >=3.9

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
LOCAL_TZ = ZoneInfo("America/New_York")  # replace with your timezone

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------
# HELPERS
# ---------------------------
def notion_date(dt):
    if not dt:
        return {"date": {"start": None}}
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
            except Exception:
                return {"date": {"start": None}}
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    return {"number": float(value) if value is not None else 0}

def notion_select(value):
    return {"select": {"name": str(value) if value else "N/A"}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value) if value else "N/A"}}]}

def notion_text(value):
    return {"rich_text": [{"text": {"content": str(value) if value else "N/A"}}]}

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

def convert_gmt_to_local(ts):
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)
        return dt.astimezone(LOCAL_TZ)
    except:
        return None

# ---------------------------
# MAIN SCRIPT
# ---------------------------
if __name__ == "__main__":
    try:
        # ---------------------------
        # DATE SETUP
        # ---------------------------
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime("%m/%d/%Y")  # For title display
        logging.info(f"📅 Collecting Garmin data for {yesterday_str}")

        # ---------------------------
        # LOGIN
        # ---------------------------
        logging.info("🔐 Logging into Garmin...")
        notion = Client(auth=NOTION_TOKEN)
        garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
        garmin.login()

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
        # DEBUG RAW DATA
        # ---------------------------
        logging.info("🔍 RAW Garmin Data Snapshots")
        logging.info("=== sleep_daily ===")
        pprint.pprint(sleep_data.get("dailySleepDTO", {}))
        logging.info("=== body_battery ===")
        pprint.pprint(body_battery)
        logging.info("=== readiness ===")
        pprint.pprint(readiness)
        logging.info("=== stats ===")
        pprint.pprint(stats)

        # ---------------------------
        # PARSE HEALTH METRICS
        # ---------------------------
        sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}

        # --- Sleep Score ---
        sleep_score = (
            sleep_daily.get("sleepScoreFeedback")
            or extract_value(sleep_daily, ["sleepScore", "score", "overallScore"])
            or 0
        )

        # --- Sleep times ---
        bed_time = convert_gmt_to_local(sleep_daily.get("sleepStartTimestampGMT"))
        wake_time = convert_gmt_to_local(sleep_daily.get("sleepEndTimestampGMT"))

        # --- Body Battery Min/Max ---
        body_battery_combined = "N/A"
        if isinstance(body_battery, list) and len(body_battery) > 0:
            bb = body_battery[0]
            arr = bb.get("bodyBatteryValuesArray") or []
            values = [v[1] for v in arr if isinstance(v, list) and len(v) > 1]
            if values:
                body_battery_combined = f"{min(values)} / {max(values)}"

        # --- Body Weight ---
        body_weight = None
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # --- Training Readiness ---
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or 0

        # --- Training Status ---
        training_status_map = {2: "Maintaining", 3: "Recovery", 4: "Productive", 5: "Peaking"}
        training_status_val = extract_value(status, ["trainingStatus", "status", "unknown_2", "currentStatus"])

        feedback_hint = extract_value(body_battery, ["feedbackShortType", "feedbackLongType"]) or ""
        readiness_feedback = ""
        if readiness and isinstance(readiness, list) and len(readiness) > 0:
            readiness_feedback = readiness[0].get("trainingFeedback", "")

        if "RECOVERING" in str(feedback_hint).upper() or "RECOVERING" in str(readiness_feedback).upper():
            training_status_val = "Recovery"
        elif isinstance(training_status_val, (int, float)):
            training_status_val = training_status_map.get(int(training_status_val), "Maintaining")
        else:
            training_status_val = "Maintaining"

        # --- Stress / HR / Calories / Steps ---
        stress = extract_value(stats, ["avgSleepStress", "stressLevelAvg", "stressScore"]) or 0
        resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"]) or 0
        calories = extract_value(stats, ["totalKilocalories", "active_calories"]) or 0
        steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

        # ---------------------------
        # DEBUG PARSED METRICS
        # ---------------------------
        logging.info("🧠 Garmin health metrics summary:")
        logging.info(f"  Steps: {steps_total}")
        logging.info(f"  Body Weight: {body_weight or 'N/A'}")
        logging.info(f"  Body Battery (Min/Max): {body_battery_combined}")
        logging.info(f"  Sleep Score: {sleep_score}")
        logging.info(f"  Bedtime: {bed_time or 'N/A'}")
        logging.info(f"  Wake Time: {wake_time or 'N/A'}")
        logging.info(f"  Training Readiness: {training_readiness}")
        logging.info(f"  Training Status: {training_status_val}")
        logging.info(f"  Resting HR: {resting_hr}")
        logging.info(f"  Stress: {stress}")
        logging.info(f"  Calories: {calories}")

        # ---------------------------
        # PUSH TO NOTION
        # ---------------------------
        health_props = {
            "Name": notion_title(yesterday_str),
            "Date": notion_date(yesterday),
            "Steps": notion_number(steps_total),
            "Body Weight": notion_number(body_weight),
            "Body Battery (Min/Max)": notion_text(body_battery_combined),
            "Sleep Score": notion_number(sleep_score),
            "Bedtime": notion_date(bed_time),
            "Wake Time": notion_date(wake_time),
            "Training Readiness": notion_number(training_readiness),
            "Training Status": notion_select(training_status_val),
            "Resting HR": notion_number(resting_hr),
            "Stress": notion_number(stress),
            "Calories Burned": notion_number(calories),
        }

        logging.info("📤 Pushing the following properties to Notion:")
        pprint.pprint(health_props)

        try:
            result = notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
            logging.info("✅ Notion API response:")
            pprint.pprint(result)
        except Exception as e:
            logging.error(f"⚠️ Failed to push health metrics: {e}")

        # ---------------------------
        # PUSH ACTIVITIES
        # ---------------------------
        logging.info(f"📤 Syncing {len(activities)} activities...")
        for act in activities:
            act_date = act.get("startTimeLocal", "")[:10] or yesterday.strftime("%Y-%m-%d")
            activity_props = {
                "Date": notion_date(act_date),
                "Name": notion_title(act.get("activityName") or f"Activity {act_date}"),
                "Distance (km)": notion_number((act.get("distance") or 0) / 1000),
                "Calories": notion_number(act.get("calories")),
                "Duration (min)": notion_number(round((act.get("duration") or 0) / 60, 1)),
                "Type": notion_select(act
