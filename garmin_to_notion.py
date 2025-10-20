#!/usr/bin/env python3
"""
Garmin -> Notion sync script
- Health metrics: pushes yesterday's metrics
- Activities: pushes new activities since last Notion entry
"""

import os
import datetime
import logging
import pprint
from notion_client import Client
from garminconnect import Garmin

# ---------------------------
# HELPERS
# ---------------------------
def safe_fetch(func, *args, **kwargs):
    """Call Garmin API safely; return None on error."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"⚠️ Error fetching from Garmin API ({func.__name__}): {e}")
        return None

def parse_garmin_datetime(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.datetime.fromisoformat(dt_str).isoformat()
    except Exception:
        pass
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"]
    for f in fmts:
        try:
            dt = datetime.datetime.strptime(dt_str, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
            return dt.isoformat()
        except Exception:
            continue
    try:
        dt = datetime.datetime.strptime(dt_str[:10], "%Y-%m-%d")
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
        return dt.isoformat()
    except Exception:
        return None

def notion_date_obj_from_iso(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_str).astimezone(LOCAL_TZ)
        return {"date": {"start": dt.isoformat()}}
    except Exception:
        return None

def notion_number(value):
    if value is None:
        return None
    try:
        v = float(value)
        return None if v == 0 else {"number": v}
    except Exception:
        return None

def notion_select(name):
    if not name:
        return None
    return {"select": {"name": str(name)}}

def notion_title(text):
    return {"title": [{"text": {"content": str(text)}}]}

def notion_text(value):
    if value is None or value == "":
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}

def extract_value(data, keys):
    if not data:
        return None
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                val = data[k]
                if isinstance(val, (int, float, str)):
                    return val
                res = extract_value(val, keys)
                if res is not None:
                    return res
        for v in data.values():
            res = extract_value(v, keys)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = extract_value(item, keys)
            if res is not None:
                return res
    return None

def filter_props(props):
    clean = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, dict) and not v:
            continue
        clean[k] = v
    return clean

def get_latest_activity_date(notion_client, activities_db_id):
    try:
        res = notion_client.databases.query(
            database_id=activities_db_id,
            page_size=1,
            sorts=[{"property": "Date", "direction": "descending"}]
        )
        results = res.get("results", [])
        if not results:
            return None
        props = results[0].get("properties", {})
        start = props.get("Date", {}).get("date", {}).get("start")
        if not start:
            return None
        dt = datetime.datetime.fromisoformat(start)
        return dt.date()
    except Exception as e:
        logger.warning(f"Could not query Notion for latest activity date: {e}")
        return None

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo
GARMIN_ACTIVITY_FETCH_LIMIT = 200

GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("garmin_to_notion")
if DEBUG:
    logger.setLevel(logging.DEBUG)

# ---------------------------
# TRAINING STATUS MAP
# ---------------------------
TRAINING_STATUS_MAP = {0:"No Status",1:"Detraining",2:"Maintaining",3:"Recovery",
                       4:"Productive",5:"Peaking",6:"Strained",7:"Unproductive",
                       8:"Overreaching",9:"Paused"}
TRAINING_EFFECT_MAP = {0:"Unknown",1:"Recovery",2:"Aerobic Base",3:"Tempo",
                       4:"Lactate Threshold",5:"Vo2Max",6:"Anaerobic Capacity"}

# ---------------------------
# BUILD PROPERTIES
# ---------------------------
def build_health_properties(yesterday_iso, steps_total, body_weight, bb_min, bb_max, sleep_score,
                            bed_time_iso, wake_time_iso, training_readiness, training_status_val,
                            resting_hr, calories):
    props = {
        "Name": notion_title(yesterday_iso.strftime("%m/%d/%Y")),
        "Date": notion_date_obj_from_iso(yesterday_iso.isoformat()),
        "Steps": notion_number(steps_total),
        "Body Weight": notion_number(body_weight),
        "Body Battery (Min)": notion_number(bb_min),
        "Body Battery (Max)": notion_number(bb_max),
        "Sleep Score": notion_number(sleep_score),
        "Bedtime": notion_date_obj_from_iso(bed_time_iso) if bed_time_iso else None,
        "Wake Time": notion_date_obj_from_iso(wake_time_iso) if wake_time_iso else None,
        "Training Readiness": notion_number(training_readiness),
        "Training Status": notion_select(training_status_val),
        "Resting HR": notion_number(resting_hr),
        "Calories Burned": notion_number(calories)
    }
    return filter_props(props)

def build_activity_properties(act_iso, activity_name, distance_km, duration_min,
                              avg_pace_km_text, avg_pace_mi_text, calories,
                              activity_type, ae_effect, an_effect):
    props = {
        "Activity Name": notion_title(activity_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(distance_km),
        "Distance (mi)": notion_number(distance_km * 0.621371) if distance_km else None,
        "Duration (mins)": notion_number(duration_min),
        "Avg Pace (min/km)": notion_text(avg_pace_km_text),
        "Avg Pace (min/mi)": notion_text(avg_pace_mi_text),
        "Calories": notion_number(calories),
        "Activity Type": notion_select(activity_type),
        "Aerobic": notion_number(ae_effect),
        "Anaerobic": notion_number(an_effect),
        "AE:AN": notion_number((ae_effect / an_effect) if (ae_effect and an_effect) else None)
    }
    return filter_props(props)

# ---------------------------
# MAIN
# ---------------------------
def main():
    if not (GARMIN_USERNAME and GARMIN_PASSWORD and NOTION_TOKEN and NOTION_HEALTH_DB_ID and NOTION_ACTIVITIES_DB_ID):
        logger.error("Missing required environment variables")
        return

    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    logger.info("Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logger.error(f"Failed to login to Garmin: {e}")
        return

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    # ---------------------------
    # HEALTH METRICS
    # ---------------------------
    steps = safe_fetch(garmin.get_daily_steps, yesterday.isoformat(), yesterday.isoformat()) or []
    sleep_data = safe_fetch(garmin.get_sleep_data, yesterday.isoformat()) or {}
    body_battery = safe_fetch(garmin.get_body_battery, yesterday.isoformat(), yesterday.isoformat()) or []
    body_comp = safe_fetch(garmin.get_body_composition, yesterday.isoformat()) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yesterday.isoformat()) or []
    status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or []
    stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

    steps_total = sum(i.get("totalSteps",0) for i in steps) if steps else None
    body_weight = None
    if body_comp and body_comp.get("dateWeightList"):
        w = body_comp["dateWeightList"][0].get("weight")
        if w:
            body_weight = round(float(w)/453.592,2)

    sleep_score = extract_value(sleep_data.get("dailySleepDTO",{}),["sleepScores","overall","value"])
    bed_ts = sleep_data.get("dailySleepDTO",{}).get("sleepStartTimestampGMT")
    wake_ts = sleep_data.get("dailySleepDTO",{}).get("sleepEndTimestampGMT")
    bed_iso = datetime.datetime.fromtimestamp(bed_ts/1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if bed_ts else None
    wake_iso = datetime.datetime.fromtimestamp(wake_ts/1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if wake_ts else None

    training_readiness = extract_value(readiness,["score","trainingReadinessScore"]) or None
    current_status_val = extract_value(status,["currentStatus","trainingStatus"])
    logger.info(f"Raw training status from Garmin: {current_status_val}")
    training_status_val = TRAINING_STATUS_MAP.get(int(current_status_val), str(current_status_val)) if current_status_val else None

    bb_min = bb_max = None
    if body_battery and isinstance(body_battery, list) and body_battery:
        try:
            values = []
            for item in body_battery:
                array = item.get("bodyBatteryValuesArray") or []
                for v in array:
                    if isinstance(v,(list,tuple)) and v[1] is not None:
                        values.append(v[1])
            if values:
                bb_min = min(values)
                bb_max = max(values)
        except Exception:
            bb_min = bb_max = None

    stats_obj = stats[0] if isinstance(stats,list) and stats else stats if isinstance(stats,dict) else {}
    calories = safe_fetch(lambda: extract_value(stats_obj, ["totalKilocalories","active_calories"])) or None
    resting_hr = safe_fetch(lambda: extract_value(stats_obj, ["restingHeartRate","heart_rate"])) or None

    health_props = build_health_properties(
        yesterday, steps_total, body_weight, bb_min, bb_max,
        sleep_score, bed_iso, wake_iso, training_readiness,
        training_status_val, resting_hr, calories
    )

    if "Name" not in health_props or "Date" not in health_props:
        logger.error("Health properties missing required Name/Date; skipping")
    else:
        try:
            notion.pages.create(parent={"database_id":NOTION_HEALTH_DB_ID}, properties=health_props)
            logger.info("✅ Synced health metrics")
        except Exception as e:
            logger.error(f"Failed to push health metrics: {e}")
            pprint.pprint(health_props)

    # ---------------------------
    # ACTIVITIES
    # ---------------------------
    last_date = get_latest_activity_date(notion, NOTION_ACTIVITIES_DB_ID)
    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    new_activities = []

    for act in activities:
        raw_dt = act.get("startTimeLocal") or act.get("startTimeGMT") or ""
        parsed_iso = parse_garmin_datetime(raw_dt)
        if not parsed_iso:
            continue
        dt_date = datetime.datetime.fromisoformat(parsed_iso).date()
        if not last_date or dt_date > last_date:
            new_activities.append((act, parsed_iso))

    logger.info(f"Found {len(new_activities)} new activities to push")

    for act, act_iso in new_activities:
        activity_name = act.get("activityName") or f"Activity {act_iso[:10]}"
        distance_km = float(act.get("distance",0))/1000 if act.get("distance") else None
        duration_min = round(float(act.get("duration",0))/60,2) if act.get("duration") else None

        avg_pace_km_text = avg_pace_mi_text = None
        if distance_km and duration_min:
            pace_km = duration_min/distance_km
            m = int(pace_km)
            s = int(round((pace_km - m)*60))
            avg_pace_km_text = f"{m}:{s:02d} min/km"
            pace_mi = pace_km/0.621371
            m = int(pace_mi)
            s = int(round((pace_mi - m)*60))
            avg_pace_mi_text = f"{m}:{s:02d} min/mi"

        calories = act.get("calories")
        ae_effect = act.get("aerobicTrainingEffect") or extract_value(act,["aeEffect"])
        an_effect = act.get("anaerobicTrainingEffect") or extract_value(act,["anEffect"])
        activity_type = act.get("activityType",{}).get("typeKey","").lower()

        activity_props = build_activity_properties(
            act_iso, activity_name, distance_km, duration_min,
            avg_pace_km_text, avg_pace_mi_text, calories,
            activity_type, ae_effect, an_effect
        )

        if "Activity Name" not in activity_props or "Date" not in activity_props:
            logger.warning(f"Skipping activity (missing required props): {activity_name}")
            continue

        try:
            notion.pages.create(parent={"database_id":NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
            logger.info(f"🏃 Logged new activity: {activity_name}")
        except Exception as e:
            logger.error(f"Failed to push activity {activity_name}: {e}")
            pprint.pprint(activity_props)

    try:
        garmin.logout()
    except Exception:
        pass
    logger.info("🏁 Sync complete.")

if __name__=="__main__":
    main()
