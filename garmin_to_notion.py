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
BACKFILL_DAYS = 14  # How many past days to backfill

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
    try:
        return {"number": float(value)} if value is not None else {"number": 0}
    except:
        return {"number": 0}

def notion_select(value):
    return {"select": {"name": str(value) if value else "Maintaining"}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value) if value else "N/A"}}]}

def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
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
# ACTIVITY UTILITIES
# ---------------------------
def format_activity_type(activity_type, activity_name=""):
    formatted_type = activity_type.replace('_', ' ').title() if activity_type else "Unknown"
    activity_subtype = formatted_type
    mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running"
    }
    if formatted_type in mapping:
        return mapping[formatted_type], formatted_type
    if "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if "barre" in activity_name.lower():
        return "Strength", "Barre"
    if "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return formatted_type, formatted_type

def format_training_effect(label):
    return label.replace('_', ' ').title() if label else "Unknown"

def activity_exists(client, database_id, activity_date, activity_type, activity_name):
    query = client.databases.query(
        database_id=database_id,
        filter={
            "and": [
                {"property": "Date", "date": {"equals": activity_date.split('T')[0]}},
                {"property": "Activity Name", "title": {"equals": activity_name}},
                {"property": "Activity Type", "select": {"equals": activity_type}}
            ]
        }
    )
    results = query['results']
    return results[0] if results else None

def needs_update(existing, new_props):
    """Check if any property differs between existing Notion page and new data."""
    existing_props = existing.get('properties', {})
    for key, value in new_props.items():
        if key not in existing_props:
            return True
        existing_val = existing_props[key]
        if "number" in value:
            if existing_val.get("number") != value["number"]:
                return True
        elif "select" in value:
            if existing_val.get("select", {}).get("name") != value["select"]["name"]:
                return True
        elif "title" in value:
            if existing_val.get("title", [{}])[0].get("text", {}).get("content") != value["title"][0]["text"]["content"]:
                return True
        elif "date" in value:
            if existing_val.get("date", {}).get("start") != value["date"]["start"]:
                return True
    return False

# ---------------------------
# MAIN SCRIPT
# ---------------------------
def main():
    today = datetime.date.today()
    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    garmin.login()

    for delta in range(BACKFILL_DAYS):
        day = today - datetime.timedelta(days=delta)
        formatted_date = day.strftime("%m/%d/%Y")
        iso_day = day.isoformat()
        logging.info(f"📅 Collecting Garmin data for {formatted_date}")

        # ---------------------------
        # FETCH DATA
        # ---------------------------
        activities = safe_fetch(garmin.get_activities, 0, 1000) or []
        steps = safe_fetch(garmin.get_daily_steps, iso_day, iso_day) or []
        sleep_data = safe_fetch(garmin.get_sleep_data, iso_day) or {}
        body_battery = safe_fetch(garmin.get_body_battery, iso_day, iso_day) or []
        body_comp = safe_fetch(garmin.get_body_composition, iso_day) or {}
        readiness = safe_fetch(garmin.get_training_readiness, iso_day) or []
        status = safe_fetch(garmin.get_training_status, iso_day) or []
        stats = safe_fetch(garmin.get_stats_and_body, iso_day) or []

        # ---------------------------
        # PARSE HEALTH METRICS
        # ---------------------------
        sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
        sleep_score = extract_value(sleep_daily, ["sleepScores", "overall", "value"]) or 0
        bed_time = sleep_daily.get("sleepStartTimestampGMT")
        wake_time = sleep_daily.get("sleepEndTimestampGMT")
        if bed_time:
            bed_time = datetime.datetime.fromtimestamp(bed_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
        if wake_time:
            wake_time = datetime.datetime.fromtimestamp(wake_time / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)

        # Body Battery
        bb_values = body_battery[0].get("bodyBatteryValuesArray") if body_battery else []
        numeric_values = [v[1] for v in bb_values if isinstance(v, list) and v[1] is not None]
        body_battery_min = min(numeric_values) if numeric_values else 0
        body_battery_max = max(numeric_values) if numeric_values else 0

        # Body Weight
        body_weight = None
        if body_comp.get("dateWeightList"):
            w_raw = body_comp["dateWeightList"][0].get("weight")
            if w_raw:
                body_weight = round(float(w_raw) / 453.592, 2)

        # Training Readiness & Status
        training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or 0
        status_map = {0:"No Status",1:"Detraining",2:"Maintaining",3:"Recovery",
                      4:"Productive",5:"Peaking",6:"Strained",7:"Unproductive",8:"Overreaching",9:"Paused"}
        current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
        training_status_val = status_map.get(int(current_status_val), "Maintaining") if isinstance(current_status_val,(int,float)) else "Maintaining"

        feedback_fields = []
        if isinstance(readiness, list) and readiness:
            feedback_fields.append(str(readiness[0].get("trainingFeedback","")).upper())
        if body_battery and isinstance(body_battery, list):
            feedback_fields.append(str(extract_value(body_battery[0], ["feedbackShortType","feedbackLongType"])).upper())
        for field in feedback_fields:
            if "RECOV" in field:
                training_status_val = "Recovery"
            if "STRAIN" in field:
                training_status_val = "Strained"

        # Stats
        stats = stats[0] if isinstance(stats, list) and stats else {}
        stress = extract_value(stats, ["avgSleepStress", "stressLevelAvg", "stressScore"]) or 0
        resting_hr = extract_value(stats, ["restingHeartRate", "heart_rate"]) or 0
        calories = extract_value(stats, ["totalKilocalories", "active_calories"]) or 0
        steps_total = sum(i.get("totalSteps",0) for i in steps) if steps else 0

        # ---------------------------
        # PUSH HEALTH METRICS
        # ---------------------------
        health_props = {
            "Name": notion_title(formatted_date),
            "Date": notion_date(iso_day),
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
            "Calories Burned": notion_number(calories)
        }

        try:
            notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
            logging.info(f"✅ Synced health metrics for {formatted_date}")
        except Exception as e:
            logging.error(f"⚠️ Failed to push health metrics: {e}")
            pprint.pprint(health_props)

        # ---------------------------
        # PUSH ACTIVITIES
        # ---------------------------
        logging.info(f"📤 Syncing {len(activities)} activities...")
        for act in activities:
            act_date = act.get("startTimeLocal", iso_day)
            distance_km = (act.get("distance") or 0)/1000
            duration_min = round((act.get("duration") or 0)/60,1)
            avg_pace = duration_min/distance_km if distance_km>0 else None

            activity_type, activity_subtype = format_activity_type(act.get("activityType",{}).get("typeKey","Unknown"), act.get("activityName",""))
            activity_name = act.get("activityName","Activity")

            activity_props = {
                "Date": notion_date(act_date),
                "Activity Name": notion_title(activity_name),
                "Distance (km)": notion_number(distance_km),
                "Duration (min)": notion_number(duration_min),
                "Avg Pace": notion_number(avg_pace),
                "Calories": notion_number(act.get("calories")),
                "Activity Type": notion_select(activity_type),
                "Subactivity Type": notion_select(activity_subtype)
            }

            existing_activity = activity_exists(notion, NOTION_ACTIVITIES_DB_ID, act_date, activity_type, activity_name)
            try:
                if existing_activity:
                    if needs_update(existing_activity, activity_props):
                        notion.pages.update(page_id=existing_activity['id'], properties=activity_props)
                        logging.info(f"🔄 Updated activity: {activity_name}")
                    else:
                        logging.info(f"🏃 Activity exists and up-to-date: {activity_name}")
                else:
                    notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
                    logging.info(f"🏃 Logged new activity: {activity_name}")
            except Exception as e:
                logging.error(f"⚠️ Failed to sync activity {activity_name}: {e}")
                pprint.pprint(activity_props)

    garmin.logout()
    logging.info("🏁 Full sync complete.")

if __name__ == "__main__":
    main()
