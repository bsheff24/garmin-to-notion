import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True

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
# HELPERS
# ---------------------------
def notion_date(dt):
    if not dt:
        dt = datetime.datetime.now()
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
            except Exception:
                dt = datetime.datetime.now()
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    try:
        return {"number": float(value) if value is not None else 0}
    except:
        return {"number": 0}

def notion_select(value):
    return {"select": {"name": str(value) if value else "Maintaining"}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value) if value else "N/A"}}]}

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
# MAIN SCRIPT
# ---------------------------
if __name__ == "__main__":
    try:
        # ---------------------------
        # DATE SETUP
        # ---------------------------
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        formatted_date = yesterday.strftime("%m/%d/%Y")
        logging.info(f"📅 Collecting Garmin data for {formatted_date}")

        # ---------------------------
        # LOGIN
        # ---------------------------
        notion = Client(auth=NOTION_TOKEN)
        garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
        garmin.login()

        # ---------------------------
        # FETCH GARMIN DATA
        # ---------------------------
        activities = safe_fetch(garmin.get_activities, 0, 10) or []
        steps = safe_fetch(garmin.get_daily_steps, yesterday.isoformat(), yesterday.isoformat()) or []
        sleep_data = safe_fetch(garmin.get_sleep_data, yesterday.isoformat()) or {}
        body_battery = safe_fetch(garmin.get_body_battery, yesterday.isoformat(), yesterday.isoformat()) or []
        body_comp = safe_fetch(garmin.get_body_composition, yesterday.isoformat()) or {}
        readiness = safe_fetch(garmin.get_training_readiness, yesterday.isoformat()) or []
        status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or []
        stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

        # ---------------------------
        # PARSE HEALTH METRICS
        # ---------------------------
        sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}

        # --- Sleep Score ---
        sleep_score = sleep_daily.get("sleepScore") or sleep_daily.get("overallScore") or 0
        sleep_score = max(0, min(sleep_score, 100))

        # --- Bedtime / Wake Time ---
        bed_time = sleep_daily.get("sleepStartTimestampGMT")
        wake_time = sleep_daily.get("sleepEndTimestampGMT")
        if bed_time:
            bed_time = datetime.datetime.fromtimestamp(bed_time / 1000)
        else:
            bed_time = datetime.datetime.now()
        if wake_time:
            wake_time = datetime.datetime.fromtimestamp(wake_time / 1000)
        else:
            wake_time = datetime.datetime.now()

        # --- Body Battery Min/Max ---
        body_battery_min = 0
        body_battery_max = 0
        if isinstance(body_battery, list) and len(body_battery) > 0:
            bb = body_battery[0]
            values = bb.get("bodyBatteryValuesArray") or []
            numeric_values = [v[1] for v in values if isinstance(v, list) and v[1] is not None]
            if numeric_values:
                body_battery_min = min(numeric_values)
                body_battery_max = max(numeric_values)

        # --- Body Weight ---
        body_weight = 0
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # --- Training Readiness ---
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or 0

        # --- Training Status ---
        training_status_val = "Maintaining"
        status_map = {
            0: "No Status",
            1: "Detraining",
            2: "Maintaining",
            3: "Recovery",
            4: "Productive",
            5: "Peaking",
            6: "Strained",
            7: "Unproductive"
        }
        current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
        if isinstance(current_status_val, (int, float)):
            training_status_val = status_map.get(int(current_status_val), "Maintaining")

        # Override based on feedback
        feedback_fields = []
        if isinstance(readiness, list) and readiness:
            feedback_fields.append(str(readiness[0].get("trainingFeedback", "")).upper())
        if isinstance(body_battery, list) and body_battery:
            feedback_fields.append(str(extract_value(body_battery[0], ["feedbackShortType", "feedbackLongType"])).upper())
        for field in feedback_fields:
            if "RECOV" in field or "RECOVER" in field:
                training_status_val = "Recovery"
                break

        # --- Steps / HR / Calories ---
        if isinstance(stats, list) and stats:
            stats = stats[0]
        stress = extract_value(stats, ["avgSleepStress", "stressLevelAvg", "stressScore"]) or 0
        resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"]) or 0
        calories = extract_value(stats, ["totalKilocalories", "active_calories"]) or 0
        steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

        # ---------------------------
        # DEBUG LOGS
        # ---------------------------
        if DEBUG:
            logging.info("🔍 Parsed Garmin metrics:")
            logging.info(f"Steps: {steps_total}, Body Weight: {body_weight}")
            logging.info(f"Body Battery Min: {body_battery_min}, Max: {body_battery_max}")
            logging.info(f"Sleep Score: {sleep_score}, Bedtime: {bed_time}, Wake Time: {wake_time}")
            logging.info(f"Training Readiness: {training_readiness}, Training Status: {training_status_val}")
            logging.info(f"Resting HR: {resting_hr}, Stress: {stress}, Calories: {calories}")

        # ---------------------------
        # PUSH TO NOTION
        # ---------------------------
        health_props = {
            "Name": notion_title(formatted_date),
            "Date": notion_date(formatted_date),
            "Steps": notion_number(steps_total),
            "Body Weight": notion_number(body_weight),
            "Body Battery (Min)": notion_number(body_battery_min),
            "Body Battery (Max)": notion_number(body_battery_max),
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
        pprint.pprint(health_props)

        try:
            notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
            logging.info(f"✅ Synced health metrics for {formatted_date}")
        except Exception as e:
            logging.error(f"⚠️ Failed to push health metrics: {e}")

        # ---------------------------
        # PUSH ACTIVITIES
        # ---------------------------
        logging.info(f"📤 Syncing {len(activities)} activities...")
        for act in activities:
            act_date = act.get("startTimeLocal", "")[:10] or yesterday.isoformat()
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

    except Exception as e:
        logging.exception(f"❌ Unexpected error: {e}")

