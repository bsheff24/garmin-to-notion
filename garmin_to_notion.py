import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client
from zoneinfo import ZoneInfo  # Python >=3.9

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = ZoneInfo("America/New_York")  # replace with your timezone

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
    try:
        return {"number": float(value) if value is not None else 0}
    except:
        return {"number": 0}

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
        status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or {}
        stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

        if DEBUG:
            logging.info("🔍 RAW GARMIN DATA")
            logging.info("Activities:")
            pprint.pprint(activities)
            logging.info("Steps:")
            pprint.pprint(steps)
            logging.info("Sleep Data:")
            pprint.pprint(sleep_data)
            logging.info("Body Battery:")
            pprint.pprint(body_battery)
            logging.info("Body Composition:")
            pprint.pprint(body_comp)
            logging.info("Training Readiness:")
            pprint.pprint(readiness)
            logging.info("Training Status:")
            pprint.pprint(status)
            logging.info("Stats:")
            pprint.pprint(stats)

        # ---------------------------
        # PARSE HEALTH METRICS
        # ---------------------------
        sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}

        # --- Sleep Score (Fixed) ---
        sleep_score = None
        for key in ["sleepScore", "overallScore"]:
            sleep_score = sleep_daily.get(key)
            if sleep_score is not None:
                break
        if sleep_score is None or sleep_score <= 25:
            scores_dict = sleep_daily.get("sleepScores", {})
            if scores_dict:
                deep = scores_dict.get("deepPercentage", {}).get("value", 0)
                light = scores_dict.get("lightPercentage", {}).get("value", 0)
                awake = scores_dict.get("awakeCount", {}).get("value", 0)
                sleep_score = min(max(deep + light - awake, 0), 100)
        sleep_score = sleep_score or 0

        bed_time = convert_gmt_to_local(sleep_daily.get("sleepStartTimestampGMT"))
        wake_time = convert_gmt_to_local(sleep_daily.get("sleepEndTimestampGMT"))

        # --- Body Battery Min/Max ---
        body_battery_combined = "N/A"
        if isinstance(body_battery, list) and body_battery:
            bb = body_battery[0]
            arr = bb.get("bodyBatteryValuesArray") or []
            values = [v[1] for v in arr if isinstance(v, list) and len(v) > 1]
            if values:
                body_battery_combined = f"{min(values)} / {max(values)}"

        # --- Body Weight ---
        body_weight = 0
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # --- Training Readiness ---
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or 0

        # --- Training Status (Fixed) ---
        training_status_val = "Maintaining"
        feedback_fields = []

        if isinstance(readiness, list) and readiness:
            feedback_fields.append(str(readiness[0].get("trainingFeedback", "")).upper())
        if isinstance(body_battery, list) and body_battery:
            feedback_fields.append(str(extract_value(body_battery[0], ["feedbackShortType", "feedbackLongType"])).upper())

        for field in feedback_fields:
            if "RECOV" in field or "RECOVER" in field:
                training_status_val = "Recovery"
                break

        if training_status_val not in ["Recovery"]:
            training_status_map = {2: "Maintaining", 3: "Recovery", 4: "Productive", 5: "Peaking"}
            if isinstance(readiness, list) and readiness:
                score = readiness[0].get("score")
                if isinstance(score, (int, float)):
                    training_status_val = training_status_map.get(int(score), "Maintaining")

        # --- Other Stats ---
        if isinstance(stats, list) and stats:
            stats = stats[0]
        stress = extract_value(stats, ["avgSleepStress", "stressLevelAvg", "stressScore"]) or 0
        resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"]) or 0
        calories = extract_value(stats, ["totalKilocalories", "active_calories"]) or 0
        steps_total = sum(i.get("totalSteps", 0) for i in steps) if isinstance(steps, list) else 0

        # ---------------------------
        # DEBUG WITH SANITY HIGHLIGHTS
        # ---------------------------
        if DEBUG:
            logging.info("🔍 PARSED GARMIN METRICS (with sanity checks)")

            def highlight(value, name, min_val=None, max_val=None, allowed_values=None):
                msg = f"{name}: {value}"
                if allowed_values and value not in allowed_values:
                    msg += " ⚠️ Unexpected value!"
                if min_val is not None and value < min_val:
                    msg += f" ⚠️ Below expected ({min_val})"
                if max_val is not None and value > max_val:
                    msg += f" ⚠️ Above expected ({max_val})"
                logging.info(msg)

            highlight(steps_total, "Steps", min_val=0)
            highlight(body_weight, "Body Weight", min_val=0)

            try:
                min_bb, max_bb = map(int, body_battery_combined.split(" / "))
            except:
                min_bb = max_bb = None
            if min_bb is not None and max_bb is not None:
                highlight(min_bb, "Body Battery Min", 0, 100)
                highlight(max_bb, "Body Battery Max", 0, 100)

            highlight(sleep_score, "Sleep Score", 0, 100)
            highlight(training_status_val, "Training Status", allowed_values=["Recovery", "Maintaining", "Productive", "Peaking"])
            highlight(resting_hr, "Resting HR", 0, 220)
            highlight(calories, "Calories Burned", 0)

        # ---------------------------
        # PUSH TO NOTION
        # ---------------------------
        health_props = {
            "Name": notion_title(formatted_date),
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

        if DEBUG:
            logging.info("📤 HEALTH PROPERTIES TO PUSH TO NOTION")
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
            act_date = act.get("startTimeLocal", "")[:10] or yesterday.isoformat()
            activity_props = {
                "Date": notion_date(act_date),
                "Name": notion_title(act.get("activityName") or f"Activity {act_date}"),
                "Distance (km)": notion_number((act.get("distance") or 0) / 1000),
                "Calories": notion_number(act.get("calories")),
                "Duration (min)": notion_number(round((act.get("duration") or 0) / 60, 1)),
                "Type": notion_select(extract_value(act.get("activityType", {}), ["typeKey"])),
            }
            try:
                notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
                logging.info(f"🏃 Logged activity: {act.get('activityName')}")
            except Exception as e:
                logging.error(f"⚠️ Failed to log activity {act.get('activityName')}: {e}")

        garmin.logout()
        logging.info("🏁 Sync complete.")

    except Exception as e:
        logging.error(f"⚠️ Unhandled exception occurred: {e}", exc_info=True)
