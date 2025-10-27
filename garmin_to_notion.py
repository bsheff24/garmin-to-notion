#!/usr/bin/env python3
"""
Garmin → Notion Sync
- Health metrics (yesterday)
- Activities (new or same-day entries)
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
    fmts = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"
    ]
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
        v = round(float(value), 2)
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
    return {k: v for k, v in props.items() if v}

def get_latest_activity_date(notion_client, db_id):
    try:
        res = notion_client.databases.query(
            database_id=db_id,
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
        return datetime.datetime.fromisoformat(start).date()
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
TRAINING_STATUS_MAP = {
    0: "No Status",
    1: "Detraining",
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
    6: "Unproductive",
    7: "Productive",  # remapped per latest status
    8: "Strained",
    9: "Overreaching",
    10: "Paused"
}

# ---------------------------
# BUILDERS
# ---------------------------
def build_health_properties(yesterday_iso, steps, weight, bb_min, bb_max, sleep_score,
                            bed_time_iso, wake_time_iso, readiness, status_val, hr, calories):
    props = {
        "Name": notion_title(yesterday_iso.strftime("%m/%d/%Y")),
        "Date": notion_date_obj_from_iso(yesterday_iso.isoformat()),
        "Steps": notion_number(steps),
        "Body Weight": notion_number(weight),
        "Body Battery (Min)": notion_number(bb_min),
        "Body Battery (Max)": notion_number(bb_max),
        "Sleep Score": notion_number(sleep_score),
        "Bedtime": notion_date_obj_from_iso(bed_time_iso) if bed_time_iso else None,
        "Wake Time": notion_date_obj_from_iso(wake_time_iso) if wake_time_iso else None,
        "Training Readiness": notion_number(readiness),
        "Training Status": notion_select(status_val),
        "Resting HR": notion_number(hr),
        "Calories Burned": notion_number(calories)
    }
    return filter_props(props)

def build_activity_properties(act_iso, name, km, mins, pace_km, pace_mi, cal, act_type, ae, an):
    ratio = (ae / an) if (ae and an and an != 0) else None
    props = {
        "Activity Name": notion_title(name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(km),
        "Distance (mi)": notion_number(km * 0.621371) if km else None,
        "Duration (mins)": notion_number(mins),
        "Avg Pace (min/km)": notion_text(pace_km),
        "Avg Pace (min/mi)": notion_text(pace_mi),
        "Calories": notion_number(cal),
        "Activity Type": notion_select(act_type),
        "Aerobic": notion_number(ae),
        "Anaerobic": notion_number(an),
        "AE:AN": notion_number(ratio)
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
    logger.info("🔐 Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    # ---------------------------
    # HEALTH METRICS
    # ---------------------------
    logger.info(f"📅 Collecting Garmin health data for {yesterday}")
    steps = safe_fetch(garmin.get_daily_steps, yesterday.isoformat(), yesterday.isoformat()) or []
    sleep = safe_fetch(garmin.get_sleep_data, yesterday.isoformat()) or {}
    bb = safe_fetch(garmin.get_body_battery, yesterday.isoformat(), yesterday.isoformat()) or []
    body_comp = safe_fetch(garmin.get_body_composition, yesterday.isoformat()) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yesterday.isoformat()) or []
    status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or []
    stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

    steps_total = sum(i.get("totalSteps", 0) for i in steps)
    weight = None
    if body_comp.get("dateWeightList"):
        w = body_comp["dateWeightList"][0].get("weight")
        if w:
            weight = round(float(w) / 453.592, 2)

    sleep_score = extract_value(sleep.get("dailySleepDTO", {}), ["sleepScores", "overall", "value"])
    bed_ts = sleep.get("dailySleepDTO", {}).get("sleepStartTimestampGMT")
    wake_ts = sleep.get("dailySleepDTO", {}).get("sleepEndTimestampGMT")
    bed_iso = datetime.datetime.fromtimestamp(bed_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if bed_ts else None
    wake_iso = datetime.datetime.fromtimestamp(wake_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if wake_ts else None

    readiness_score = extract_value(readiness, ["score", "trainingReadinessScore"])
    raw_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
    logger.info(f"Raw training status from Garmin (parsed): {raw_status_val}")
    training_status_val = TRAINING_STATUS_MAP.get(int(raw_status_val), str(raw_status_val)) if raw_status_val else None

    bb_min = bb_max = None
    if isinstance(bb, list) and bb:
        vals = []
        for item in bb:
            for v in item.get("bodyBatteryValuesArray", []):
                if isinstance(v, (list, tuple)) and v[1] is not None:
                    vals.append(v[1])
        if vals:
            bb_min = min(vals)
            bb_max = max(vals)

    stats_obj = stats[0] if isinstance(stats, list) and stats else stats
    calories = extract_value(stats_obj, ["totalKilocalories", "active_calories"])
    hr = extract_value(stats_obj, ["restingHeartRate", "heart_rate"])

    health_props = build_health_properties(yesterday, steps_total, weight, bb_min, bb_max,
                                           sleep_score, bed_iso, wake_iso, readiness_score,
                                           training_status_val, hr, calories)
    try:
        notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
        logger.info("✅ Synced health metrics (yesterday)")
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
        raw_dt = act.get("startTimeLocal") or act.get("startTimeGMT")
        parsed_iso = parse_garmin_datetime(raw_dt)
        if not parsed_iso:
            continue
        dt_date = datetime.datetime.fromisoformat(parsed_iso).date()

        if not last_date or dt_date >= last_date:
            existing = notion.databases.query(
                database_id=NOTION_ACTIVITIES_DB_ID,
                filter={"property": "Activity Name", "title": {"equals": act.get("activityName")}}
            )
            if not existing.get("results"):
                new_activities.append((act, parsed_iso))

    logger.info(f"📊 Found {len(new_activities)} new activities to push")

    for act, act_iso in new_activities:
        name = act.get("activityName") or f"Activity {act_iso[:10]}"
        km = float(act.get("distance", 0)) / 1000 if act.get("distance") else None
        mins = round(float(act.get("duration", 0)) / 60, 2) if act.get("duration") else None

        pace_km = pace_mi = None
        if km and mins:
            pk = mins / km
            m, s = int(pk), int(round((pk - int(pk)) * 60))
            pace_km = f"{m}:{s:02d} min/km"
            pm = pk / 0.621371
            m, s = int(pm), int(round((pm - int(pm)) * 60))
            pace_mi = f"{m}:{s:02d} min/mi"

        ae = act.get("aerobicTrainingEffect")
        an = act.get("anaerobicTrainingEffect")
        act_type = act.get("activityType", {}).get("typeKey", "").lower()
        cal = act.get("calories")

        props = build_activity_properties(act_iso, name, km, mins, pace_km, pace_mi, cal, act_type, ae, an)
        try:
            notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=props)
            logger.info(f"🏃 Logged activity: {name}")
        except Exception as e:
            logger.error(f"Failed to push {name}: {e}")
            pprint.pprint(props)

    try:
        garmin.logout()
    except Exception:
        pass
    logger.info("🏁 Sync complete.")

if __name__ == "__main__":
    main()

