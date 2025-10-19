import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client
import pytz  # to handle local timezone conversion

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
        dt = datetime.datetime.now()
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
            except Exception:
                dt = datetime.datetime.now()
    if isinstance(dt, datetime.datetime):
        dt = dt.astimezone(LOCAL_TZ)
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    if value is None:
        return None
    try:
        return {"number": float(value)}
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
        sleep_score = extract_value(sleep_daily, ["sleepScores", "overall", "value"]) or 0

        # Bedtime/Wake Time
        bed_time = sleep_daily.get("sleepStartTimestampGMT")
        wake_time = sleep_daily.get("sleepEndTimestampGMT")
        if bed_time:
            bed_time = datetime.datetime.fromtimestamp(bed_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
        else:
            bed_time = None
        if wake_time:
            wake_time = datetime.datetime.fromtimestamp(wake_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
        else:
            wake_time = None

        # Body Battery
        body_battery_min = 0
        body_battery_max = 0
        if body_battery and isinstance(body_battery, list):
            bb_values = body_battery[0].get("bodyBatteryValuesArray") or []
            numeric_values = [v[1] for v in bb_values if isinstance(v, list) and v[1] is not None]
            if numeric_values:
                body_battery_min = min(numeric_values)
                body_battery_max = max(numeric_values)

        # Body Weight
        body_weight = None
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # Training Readiness
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or 0

        # Training Status
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
            9: "Paused"
        }
        current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
        training_status_val = status_map.get(int(current_status_val), "Maintaining") if isinstance(current_status_val, (int, float)) else "Maintaining"

        feedback_fields = []
        if isinstance(readiness, list) and readiness:
            feedback_fields.append(str(readiness[0].get("trainingFeedback", "")).upper())
        if body_battery and isinstance(body_battery, list):
            feedback_fields.append(str(extract_value(body_battery[0], ["feedbackShortType", "feedbackLongType"])).upper())
        for field in feedback_fields:
            if "RECOV" in field or "RECOVER" in field:
                training_status_val = "Recovery"
                break
            if "STRAIN" in field:
                training_status_val = "Strained"
                break

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
        training_effect_map = {
            0: "Unknown",
            1: "Recovery",
            2: "Aerobic Base",
            3: "Tempo",
            4: "Lactate Threshold",
            5: "Vo2Max",
            6: "Anaerobic Capacity"
        }
        ae_an_options = ["Overreaching","Highly Impacting","Impacting","Improving","Maintaining","Some Benefit","Recovery","No Benefit","Unknown"]

        for act in activities:
            act_date = act.get("startTimeLocal", "")[:10] or yesterday.isoformat()
            distance_km = (act.get("distance") or 0) / 1000
            duration_min = round((act.get("duration") or 0) / 60, 1)
            avg_pace = duration_min / distance_km if distance_km > 0 else None
            training_effect = training_effect_map.get(act.get("trainingEffect", 0), "Unknown")
            ae_effect = act.get("aeEffect", {}).get("value", None)
            an_effect = act.get("anEffect", {}).get("value", None)

            activity_props = {
                "Date": notion_date(act_date),
                "Activity Name": notion_title(act.get("activityName") or f"Activity {act_date}"),
                "Distance (km)": notion_number(distance_km),
                "Distance (mi)": notion_number(distance_km * 0.621371),
                "Duration (min)": notion_number(duration_min),
                "Avg Pace (min/mi)": notion_number(avg_pace),
                "Calories": notion_number(act.get("calories")),
                "Type": notion_select(act.get("activityType", {}).get("typeKey")),
                "Training Effect": notion_select(training_effect),
                "Aerobic": notion_number(ae_effect),
                "Anaerobic": notion_number(an_effect),
                "AE:AN": notion_number(ae_effect / an_effect if an_effect else None),
                "Aerobic Effect": notion_select(ae_an_options[ae_effect] if ae_effect is not None and ae_effect < len(ae_an_options) else "Unknown"),
                "Anaerobic Effect": notion_select(ae_an_options[an_effect] if an_effect is not None and an_effect < len(ae_an_options) else "Unknown")
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
