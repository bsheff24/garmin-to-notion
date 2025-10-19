import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client
import pytz

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo

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
        return {"date": None}
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except Exception:
            return {"date": None}
    if isinstance(dt, datetime.datetime):
        dt = dt.astimezone(LOCAL_TZ)
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    if value in (None, "", 0):
        return {"number": None}
    try:
        return {"number": round(float(value), 2)}
    except:
        return {"number": None}

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
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        formatted_date = yesterday.strftime("%m/%d/%Y")
        logging.info(f"📅 Collecting Garmin data for {formatted_date}")

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
        # PARSE HEALTH DATA
        # ---------------------------
        sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}

        # Sleep score
        sleep_score = 0
        if "sleepScores" in sleep_daily:
            overall = sleep_daily["sleepScores"].get("overall", {})
            sleep_score = overall.get("value", 0)
        sleep_score = max(0, min(sleep_score, 100))

        # Bedtime / Wake time (GMT → local)
        bed_time = sleep_daily.get("sleepStartTimestampGMT")
        wake_time = sleep_daily.get("sleepEndTimestampGMT")
        if bed_time:
            bed_time = datetime.datetime.fromtimestamp(bed_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
        if wake_time:
            wake_time = datetime.datetime.fromtimestamp(wake_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)

        # Body battery
        body_battery_min, body_battery_max = None, None
        if isinstance(body_battery, list) and len(body_battery) > 0:
            bb = body_battery[0]
            values = bb.get("bodyBatteryValuesArray") or []
            numeric_values = [v[1] for v in values if isinstance(v, list) and v[1] is not None]
            if numeric_values:
                body_battery_min = min(numeric_values)
                body_battery_max = max(numeric_values)

        # Weight (in lbs)
        body_weight = None
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # Training readiness
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore"]) or 0

        # Training status mapping
        status_map = {
            0: "No Status",
            1: "Detraining",
            2: "Maintaining",
            3: "Recovery",
            4: "Productive",
            5: "Peaking",
            6: "Strained",
            7: "Unproductive",
            8: "Overreaching",
            9: "Paused",
        }
        current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
        if isinstance(current_status_val, (int, float)):
            training_status_val = status_map.get(int(current_status_val), "Maintaining")
        else:
            training_status_val = "Maintaining"

        # Override logic for Recovery / Strained based on feedback
        feedback_fields = []
        if isinstance(readiness, list) and readiness:
            feedback_fields.append(str(readiness[0].get("trainingFeedback", "")).upper())
        for field in feedback_fields:
            if "RECOVER" in field:
                training_status_val = "Recovery"
            elif "STRAIN" in field:
                training_status_val = "Strained"

        # Stats
        if isinstance(stats, list) and stats:
            stats = stats[0]
        stress = extract_value(stats, ["avgSleepStress", "stressLevelAvg", "stressScore"]) or 0
        resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"]) or 0
        calories = extract_value(stats, ["totalKilocalories", "active_calories"]) or 0
        steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

        # ---------------------------
        # PUSH HEALTH METRICS
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
            "Calories Burned": notion_number(calories),
        }

        notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
        logging.info(f"✅ Synced health metrics for {formatted_date}")

        # ---------------------------
        # PUSH ACTIVITIES
        # ---------------------------
        logging.info(f"📤 Syncing {len(activities)} activities...")

        for act in activities:
            act_date = act.get("startTimeLocal", "")[:10] or yesterday.isoformat()
            distance_km = (act.get("distance") or 0) / 1000
            distance_mi = distance_km * 0.621371
            duration_min = (act.get("duration") or 0) / 60
            avg_pace = round(duration_min / distance_mi, 2) if distance_mi > 0 else None

            aerobic = act.get("aerobicTrainingEffect") or 0
            anaerobic = act.get("anaerobicTrainingEffect") or 0
            ratio = round(aerobic / anaerobic, 2) if anaerobic else None

            activity_props = {
                "Date": notion_date(act_date),
                "Activity Name": notion_title(act.get("activityName") or f"Activity {act_date}"),
                "Distance (km)": notion_number(distance_km),
                "Distance (mi)": notion_number(distance_mi),
                "Calories": notion_number(act.get("calories")),
                "Duration (min)": notion_number(duration_min),
                "Avg Pace (min/mi)": notion_number(avg_pace),
                "Type": notion_select(act.get("activityType", {}).get("typeKey")),
                "Training Effect": notion_select(act.get("trainingEffectLabel")),
                "Aerobic Effect": notion_select(act.get("aerobicTrainingEffectMessage")),
                "Anaerobic Effect": notion_select(act.get("anaerobicTrainingEffectMessage")),
                "AE:AN": notion_number(ratio),
            }

            notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
            logging.info(f"🏃 Logged activity: {act.get('activityName')}")

        garmin.logout()
        logging.info("🏁 Sync complete.")

    except Exception as e:
        logging.exception(f"❌ Unexpected error: {e}")
