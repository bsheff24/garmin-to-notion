#!/usr/bin/env python3
"""
Unified Garmin -> Notion sync script
- Health metrics: pushes yesterday's metrics to Health Metrics DB (title property "Name")
- Activities: pushes any new activities since the latest "Date" in your Activities DB
  - Activity title property: "Activity Name"
  - Date property: "Date" (date/time)
  - Avg Pace fields stored as rich_text (text) to match Text property
- Numeric fields set to None / omitted when empty or zero so Notion leaves them blank
- Full training status mapping
"""

import os
import datetime
import logging
import pprint
from notion_client import Client
from garminconnect import Garmin

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo
GARMIN_ACTIVITY_FETCH_LIMIT = 200  # fetch this many recent activities (tuneable)

# ---------------------------
# ENV VARIABLES (expected)
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
logger = logging.getLogger("garmin_to_notion")
if DEBUG:
    logger.setLevel(logging.DEBUG)

# ---------------------------
# HELPERS
# ---------------------------
def safe_fetch(func, *args, **kwargs):
    """
    Safely call a Garmin API function and handle connection errors gracefully.
    Returns None if the call fails.
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"⚠️ Error fetching from Garmin API ({func.__name__}): {e}")
        return None


def parse_garmin_datetime(dt_str):
    """Try multiple formats Garmin may return. Return ISO 8601 string or None."""
    if not dt_str:
        return None
    # If dt_str already ISO-ish with timezone, try fromisoformat direct
    try:
        # Python's fromisoformat handles many forms, but we'll catch errors
        dt = datetime.datetime.fromisoformat(dt_str)
        return dt.isoformat()
    except Exception:
        pass

    # Try common Garmin formats
    fmts = [
        "%Y-%m-%d %H:%M:%S",  # e.g. '2025-10-19 08:33:31'
        "%Y-%m-%d",           # '2025-10-19'
        "%Y-%m-%dT%H:%M:%S.%fZ", # e.g. '2025-10-19T08:33:31.000Z'
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S"
    ]
    for f in fmts:
        try:
            dt = datetime.datetime.strptime(dt_str, f)
            # treat naive datetimes as local UTC then convert
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
            return dt.isoformat()
        except Exception:
            continue
    # fallback: try take date portion
    try:
        date_part = dt_str[:10]
        dt = datetime.datetime.strptime(date_part, "%Y-%m-%d")
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
        return dt.isoformat()
    except Exception:
        return None

def notion_date_obj_from_iso(iso_str):
    """Return Notion date object for a datetime iso string or date string."""
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        dt = dt.astimezone(LOCAL_TZ)
        return {"date": {"start": dt.isoformat()}}
    except Exception:
        # try parse date only
        try:
            dt = datetime.datetime.strptime(iso_str, "%Y-%m-%d")
            dt = dt.astimezone(LOCAL_TZ)
            return {"date": {"start": dt.isoformat()}}
        except Exception:
            return None

def notion_number(value):
    """Return Notion number dict or None (so property omitted)."""
    if value is None:
        return None
    try:
        # treat 0 as None to avoid writing zeros
        v = float(value)
        if v == 0:
            return None
        return {"number": v}
    except Exception:
        return None

def notion_select(name):
    if not name and name is not False:
        return None
    if name is None:
        return None
    return {"select": {"name": str(name)}}

def notion_title(text):
    # Title must be provided for a page; caller should ensure not None
    return {"title": [{"text": {"content": str(text)}}]}

def notion_text(value):
    if value is None or value == "":
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}

def extract_value(data, keys):
    """Deep-extract first occurrence of any key in keys from nested dict/list."""
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

# Map Garmin training status codes to human strings (support all known codes)
TRAINING_STATUS_MAP = {
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

TRAINING_EFFECT_MAP = {
    0: "Unknown",
    1: "Recovery",
    2: "Aerobic Base",
    3: "Tempo",
    4: "Lactate Threshold",
    5: "Vo2Max",
    6: "Anaerobic Capacity"
}

# ---------------------------
# Notion helper: only include non-None props
# ---------------------------
def filter_props(props):
    """Return new dict containing only keys with non-None values (and non-empty nested)."""
    clean = {}
    for k, v in props.items():
        if v is None:
            continue
        # for nested dicts like {"date": {"start": ...}}
        if isinstance(v, dict) and not v:
            continue
        clean[k] = v
    return clean

# ---------------------------
# Notion helpers: read latest date in Activities DB to find last synced activity
# ---------------------------
def get_latest_activity_date(notion_client, activities_db_id):
    try:
        query_payload = {
            "database_id": activities_db_id,
            "page_size": 1,
            "sorts": [{"property": "Date", "direction": "descending"}]
        }
        res = notion_client.databases.query(**query_payload)
        results = res.get("results", [])
        if not results:
            return None
        props = results[0].get("properties", {})
        date_prop = props.get("Date", {})
        start = date_prop.get("date", {}).get("start")
        if not start:
            return None
        # normalize date to date part
        try:
            dt = datetime.datetime.fromisoformat(start)
            return dt.date()
        except Exception:
            try:
                dt = datetime.datetime.strptime(start[:10], "%Y-%m-%d")
                return dt.date()
            except Exception:
                return None
    except Exception as e:
        logger.warning(f"Could not query Notion for latest activity date: {e}")
        return None

# ---------------------------
# Build properties for health metrics and activities
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
                              activity_type, training_effect_label,
                              ae_effect, an_effect):
    props = {
        "Activity Name": notion_title(activity_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(distance_km),
        "Distance (mi)": notion_number(distance_km * 0.621371) if distance_km is not None else None,
        "Duration": notion_number(duration_min),
        "Avg Pace (min/km)": notion_text(avg_pace_km_text),
        "Avg Pace (min/mi)": notion_text(avg_pace_mi_text),
        "Calories": notion_number(calories),
        "Activity Type": notion_select(activity_type),
        "Training Type": notion_select(training_effect_label),
        "Aerobic": notion_number(ae_effect),
        "Anaerobic": notion_number(an_effect),
        "AE:AN": notion_number((ae_effect / an_effect) if (ae_effect and an_effect) else None),
        "Aerobic Effect": notion_select(ae_effect),
        "Anaerobic Effect": notion_select(an_effect)
    }
    return filter_props(props)

# ---------------------------
# MAIN
# ---------------------------
def main():
    if not (GARMIN_USERNAME and GARMIN_PASSWORD and NOTION_TOKEN and NOTION_HEALTH_DB_ID and NOTION_ACTIVITIES_DB_ID):
        logger.error("Missing required environment variables (GARMIN_USERNAME/GARMIN_PASSWORD/NOTION_TOKEN/NOTION_HEALTH_DB_ID/NOTION_ACTIVITIES_DB_ID)")
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
    iso_yesterday = yesterday

    # ---------------------------
    # HEALTH METRICS (yesterday only)
    # ---------------------------
    logger.info(f"📅 Collecting Garmin health data for {yesterday.strftime('%m/%d/%Y')}")
    steps = safe_fetch(garmin.get_daily_steps, iso_yesterday.isoformat(), iso_yesterday.isoformat()) or []
    sleep_data = safe_fetch(garmin.get_sleep_data, iso_yesterday.isoformat()) or {}
    body_battery = safe_fetch(garmin.get_body_battery, iso_yesterday.isoformat(), iso_yesterday.isoformat()) or []
    body_comp = safe_fetch(garmin.get_body_composition, iso_yesterday.isoformat()) or {}
    readiness = safe_fetch(garmin.get_training_readiness, iso_yesterday.isoformat()) or []
    status = safe_fetch(garmin.get_training_status, iso_yesterday.isoformat()) or []
    stats = safe_fetch(garmin.get_stats_and_body, iso_yesterday.isoformat()) or []

    # parse values robustly
    sleep_daily = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
    sleep_score = extract_value(sleep_daily, ["sleepScores", "overall", "value"]) or None

    bed_ts = sleep_daily.get("sleepStartTimestampGMT")
    wake_ts = sleep_daily.get("sleepEndTimestampGMT")
    bed_iso = None
    wake_iso = None
    if bed_ts:
        try:
            bed_iso = datetime.datetime.fromtimestamp(bed_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat()
        except Exception:
            bed_iso = None
    if wake_ts:
        try:
            wake_iso = datetime.datetime.fromtimestamp(wake_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat()
        except Exception:
            wake_iso = None

    # Body battery
    bb_min = None
    bb_max = None
    if isinstance(body_battery, list) and body_battery:
        try:
            bb_values = body_battery[0].get("bodyBatteryValuesArray", []) or []
            numeric_values = [v[1] for v in bb_values if isinstance(v, (list, tuple)) and v[1] is not None]
            if numeric_values:
                bb_min = min(numeric_values)
                bb_max = max(numeric_values)
        except Exception:
            bb_min = bb_max = None

    # Body weight
    body_weight = None
    if body_comp and isinstance(body_comp, dict) and body_comp.get("dateWeightList"):
        try:
            w = body_comp["dateWeightList"][0].get("weight")
            if w:
                # Garmin weight appears to be in ounces? original used /453.592 to get pounds; keep previous conversion
                body_weight = round(float(w) / 453.592, 2)
        except Exception:
            body_weight = None

    # Training Readiness and Status (map all statuses)
    training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or None
    current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
    training_status_val = None
    if isinstance(current_status_val, (int, float)):
        training_status_val = TRAINING_STATUS_MAP.get(int(current_status_val))
    elif isinstance(current_status_val, str):
        # maybe string numeric
        try:
            training_status_val = TRAINING_STATUS_MAP.get(int(current_status_val))
        except Exception:
            training_status_val = str(current_status_val)

    # Stats (calories, resting HR)
    stats_obj = stats[0] if isinstance(stats, list) and stats else (stats if isinstance(stats, dict) else {})
    calories = safe_fetch(lambda: extract_value(stats_obj, ["totalKilocalories", "active_calories"])) or None
    resting_hr = safe_fetch(lambda: extract_value(stats_obj, ["restingHeartRate", "heart_rate"])) or None
    steps_total = sum(i.get("totalSteps", 0) for i in steps) if steps else None

    health_props = build_health_properties(
        yesterday,
        steps_total,
        body_weight,
        bb_min,
        bb_max,
        sleep_score,
        bed_iso,
        wake_iso,
        training_readiness,
        training_status_val,
        resting_hr,
        calories,
    )

    # Ensure required fields for health page exist (Name and Date)
    if "Name" not in health_props or "Date" not in health_props:
        logger.error("Health properties missing required Name or Date; aborting health push")
    else:
        try:
            notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
            logger.info("✅ Synced health metrics (yesterday)")
        except Exception as e:
            logger.error(f"⚠️ Failed to push health metrics: {e}")
            pprint.pprint(health_props)

    # ---------------------------
    # ACTIVITIES: push new activities since the latest recorded date in Notion
    # ---------------------------
    logger.info("Determining latest activity date in Notion...")
    last_notions_date = get_latest_activity_date(notion, NOTION_ACTIVITIES_DB_ID)
    logger.info(f"Latest activity date in Notion: {last_notions_date}")

    # Fetch recent activities from Garmin (most recent batch)
    logger.info(f"Fetching up to {GARMIN_ACTIVITY_FETCH_LIMIT} recent activities from Garmin...")
    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Fetched {len(activities)} activities from Garmin (batch)")

    # Filter out only those after the last recorded date
    new_activities = []
    for act in activities:
        raw_dt = act.get("startTimeLocal") or act.get("startTimeGMT") or ""
        parsed_iso = parse_garmin_datetime(raw_dt)
        if not parsed_iso:
            # skip ones we cannot parse
            continue
        try:
            dt_obj = datetime.datetime.fromisoformat(parsed_iso)
            dt_date = dt_obj.date()
        except Exception:
            continue
        # If we have last_notions_date, only include strictly greater dates (i.e., newer)
        if last_notions_date:
            if dt_date > last_notions_date:
                new_activities.append((act, parsed_iso))
        else:
            # No existing entries in Notion → include all fetched activities
            new_activities.append((act, parsed_iso))

    logger.info(f"Found {len(new_activities)} candidate new activities to push")

    # Process and push (create or update) activities
    for act, act_iso in new_activities:
        # basic fields
        # Use activity name as title property in Activities DB
        activity_name = act.get("activityName") or f"Activity {act_iso[:10]}"
        # distance (garmin in meters)
        distance_km = None
        try:
            distance_m = act.get("distance")
            if distance_m is not None:
                distance_km = float(distance_m) / 1000.0
        except Exception:
            distance_km = None

        # duration: many Garmin endpoints return 'duration' in seconds (moving time)
        duration_min = None
        try:
            raw_dur = act.get("duration")
            if raw_dur is not None:
                duration_min = round(float(raw_dur) / 60.0, 2)
        except Exception:
            duration_min = None

        # compute paces as strings for text fields
        avg_pace_km_text = None
        avg_pace_mi_text = None
        try:
            if distance_km and duration_min:
                pace_min_per_km = duration_min / distance_km if distance_km else None
                if pace_min_per_km:
                    # Format as minutes:seconds maybe user prefers decimal; script writes text with minutes.decimal
                    # We'll format to min:sec for readability (e.g. 7:30)
                    minutes = int(pace_min_per_km)
                    seconds = int(round((pace_min_per_km - minutes) * 60))
                    avg_pace_km_text = f"{minutes}:{seconds:02d} min/km"
                    # convert to min/mi:
                    pace_min_per_mi = pace_min_per_km / 0.621371
                    if pace_min_per_mi:
                        m = int(pace_min_per_mi)
                        s = int(round((pace_min_per_mi - m) * 60))
                        avg_pace_mi_text = f"{m}:{s:02d} min/mi"
        except Exception:
            avg_pace_km_text = avg_pace_mi_text = None

        # calories
        calories = act.get("calories")
        # training effect label (may be present)
        training_effect_label = act.get("trainingEffectLabel") or TRAINING_EFFECT_MAP.get(act.get("trainingEffect"), "Unknown")
        ae_effect = act.get("aerobicTrainingEffect") or act.get("aeEffect", {}).get("value") or extract_value(act, ["aerobicTrainingEffect", "aeEffect"])
        an_effect = act.get("anaerobicTrainingEffect") or act.get("anEffect", {}).get("value") or extract_value(act, ["anaerobicTrainingEffect", "anEffect"])

        # Build properties and only include non-None fields
        activity_props = build_activity_properties(
            act_iso,
            activity_name,
            distance_km,
            duration_min,
            avg_pace_km_text,
            avg_pace_mi_text,
            calories,
            format_activity_type(act.get("activityType", {}).get("typeKey", ""), activity_name)[0],
            training_effect_label,
            ae_effect,
            an_effect
        )

        # Ensure required fields for Activity (Activity Name title and Date) are present
        if "Activity Name" not in activity_props or "Date" not in activity_props:
            logger.warning(f"Skipping activity (missing required props): {activity_name} / {act_iso}")
            logger.debug("Activity raw payload:")
            logger.debug(pprint.pformat(act))
            continue

        # Check if an entry exists already (match by Date, Activity Name, Activity Type)
        activity_type = activity_props.get("Activity Type", {}).get("select", {}).get("name") if activity_props.get("Activity Type") else None
        existing = None
        try:
            existing = activity_exists(notion, NOTION_ACTIVITIES_DB_ID, act_iso, activity_type, activity_name)
        except Exception as e:
            logger.warning(f"Failed to query for existing activity: {e}")

        try:
            if existing:
                # update only if changed
                if needs_update(existing, activity_props):
                    notion.pages.update(page_id=existing["id"], properties=activity_props)
                    logger.info(f"🔄 Updated activity: {activity_name} ({act_iso[:10]})")
                else:
                    logger.debug(f"Activity exists and up-to-date: {activity_name} ({act_iso[:10]})")
            else:
                notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
                logger.info(f"🏃 Logged new activity: {activity_name} ({act_iso[:10]})")
        except Exception as e:
            logger.error(f"⚠️ Failed to push activity {activity_name}: {e}")
            pprint.pprint(activity_props)

    # ---------------------------
    # Done
    # ---------------------------
    try:
        garmin.logout()
    except Exception:
        pass
    logger.info("🏁 Sync complete.")

if __name__ == "__main__":
    main()

