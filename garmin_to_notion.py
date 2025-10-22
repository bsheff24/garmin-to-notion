#!/usr/bin/env python3
"""
Garmin -> Notion unified sync
- Health metrics (yesterday) -> NOTION_HEALTH_DB_ID
- Activities -> NOTION_ACTIVITIES_DB_ID (create or update)
Features:
- Correct timezone handling (Garmin UTC -> America/Chicago)
- Subactivity populated
- Rounded numeric fields (km/mi, duration, AE/AN)
- Avg pace always populated (tries averageSpeed then duration/distance)
- Cleaned training-effect labels
- Duplicate-safe: update if Activity Name + Date + Activity Type match
- Keeps Notion templates/icons intact (no icon overwrites)
"""

import os
import datetime
import logging
import pprint
from notion_client import Client
from garminconnect import Garmin
import pytz
from dotenv import load_dotenv

# ---------------------------
# CONFIG
# ---------------------------
load_dotenv()
DEBUG = True
LOCAL_TZ = pytz.timezone("America/Chicago")
GARMIN_ACTIVITY_FETCH_LIMIT = 200

# ---------------------------
# ENV
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
# TRAINING / EFFECT MAPS
# ---------------------------
TRAINING_STATUS_MAP = {
    0: "No Status", 1: "Detraining", 2: "Maintaining", 3: "Recovery",
    4: "Productive", 5: "Peaking", 6: "Strained", 7: "Unproductive",
    8: "Overreaching", 9: "Paused"
}

# Helper to clean Garmin labels like IMPACTING_TEMPO_22 -> "Impacting"
def clean_training_label(label):
    if not label:
        return None
    s = str(label).upper()
    known = {
        "IMPROVING": "Improving",
        "IMPACTING": "Impacting",
        "HIGHLY_IMPACTING": "Highly Impacting",
        "MAINTAINING": "Maintaining",
        "RECOVERY": "Recovery",
        "NO_BENEFIT": "No Benefit",
        "NO_AEROBIC_BENEFIT": "No Benefit",
        "MINOR": "Some Benefit",
        "OVERREACHING": "Overreaching"
    }
    for k, v in known.items():
        if k in s:
            return v
    # fallback: title-case a cleaned version
    return s.replace("_", " ").title()

# ---------------------------
# HELPERS
# ---------------------------
def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"⚠️ Garmin API error ({getattr(func,'__name__', func)}): {e}")
        return None

def parse_garmin_datetime(dt_str):
    """Return a local ISO timestamp string (with offset) from various Garmin formats."""
    if not dt_str:
        return None
    s = str(dt_str).strip()
    try:
        # Zulu -> +00:00
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        # If ISO with offset, fromisoformat will work
        if "T" in s and ("+" in s[19:] or "-" in s[19:]):
            dt = datetime.datetime.fromisoformat(s)
            return dt.astimezone(LOCAL_TZ).isoformat()
        # try direct fromisoformat
        try:
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo:
                return dt.astimezone(LOCAL_TZ).isoformat()
        except Exception:
            pass
        # fallback formats (treat as UTC)
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]
        for f in fmts:
            try:
                dt = datetime.datetime.strptime(s, f)
                dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
                return dt.isoformat()
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"parse_garmin_datetime error for '{dt_str}': {e}")
    return None

def notion_date_obj_from_iso(iso_str):
    if not iso_str:
        return None
    return {"date": {"start": iso_str}}

def notion_number(value):
    if value is None:
        return None
    try:
        v = float(value)
        # Notion shows empty when None; choose to skip zeros to keep DB clean (consistent with prior behavior)
        if v == 0:
            return None
        return {"number": round(v, 2)}
    except Exception:
        return None

def notion_select(name):
    if name is None:
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

# ---------------------------
# Activity formatting helpers
# ---------------------------
def format_activity_type(activity_type, activity_name=""):
    # activity_type may be dict or string
    if isinstance(activity_type, dict):
        # common key is typeKey
        activity_type = activity_type.get("typeKey") or activity_type.get("type") or ""
    if not activity_type:
        formatted = "Unknown"
    else:
        formatted = str(activity_type).replace("_", " ").title()
    subtype = formatted
    mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running"
    }
    main = mapping.get(formatted, formatted)
    # name-based overrides
    if activity_name and "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if activity_name and "barre" in activity_name.lower():
        return "Strength", "Barre"
    if activity_name and "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return main, subtype

def compute_paces(average_speed_mps, duration_min, distance_km):
    """Return (pace_km_str, pace_mi_str) trying averageSpeed then fallback to duration/distance."""
    # try average_speed first (m/s)
    if average_speed_mps and average_speed_mps > 0:
        pace_km = 1000 / (average_speed_mps * 60)
        m = int(pace_km)
        s = int(round((pace_km - m) * 60))
        pace_km_str = f"{m}:{s:02d} min/km"
        pace_mi = 1609.34 / (average_speed_mps * 60)
        m2 = int(pace_mi)
        s2 = int(round((pace_mi - m2) * 60))
        pace_mi_str = f"{m2}:{s2:02d} min/mi"
        return pace_km_str, pace_mi_str
    # fallback: duration/distance
    try:
        if distance_km and distance_km > 0 and duration_min and duration_min > 0:
            pace_min_per_km = duration_min / distance_km
            m = int(pace_min_per_km)
            s = int(round((pace_min_per_km - m) * 60))
            pace_km_str = f"{m}:{s:02d} min/km"
            pace_min_per_mi = pace_min_per_km / 0.621371
            m2 = int(pace_min_per_mi)
            s2 = int(round((pace_min_per_mi - m2) * 60))
            pace_mi_str = f"{m2}:{s2:02d} min/mi"
            return pace_km_str, pace_mi_str
    except Exception:
        pass
    # final fallback
    return "Unknown", "Unknown"

# ---------------------------
# Build props
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
    # filter None values
    return {k: v for k, v in props.items() if v is not None}

def build_activity_properties(act_iso, activity_name, distance_km, duration_min,
                              avg_pace_km_text, avg_pace_mi_text, calories,
                              activity_type, sub_activity_type,
                              ae_effect, an_effect, training_effect_label,
                              aerobic_msg, anaerobic_msg):
    # rounding / safe conversions
    ae_val = round(float(ae_effect), 1) if ae_effect is not None else None
    an_val = round(float(an_effect), 1) if an_effect is not None else None
    ratio_val = None
    if ae_val is not None and an_val is not None and an_val != 0:
        try:
            ratio_val = round(ae_val / an_val, 2)
        except Exception:
            ratio_val = None

    dist_km_r = round(distance_km, 2) if distance_km is not None else None
    dist_mi_r = round(distance_km * 0.621371, 2) if distance_km is not None else None
    dur_r = round(duration_min, 2) if duration_min is not None else None

    props = {
        "Activity Name": notion_title(activity_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(dist_km_r),
        "Distance (mi)": notion_number(dist_mi_r),
        "Duration (mins)": notion_number(dur_r),
        "Avg Pace (min/km)": notion_text(avg_pace_km_text),
        "Avg Pace (min/mi)": notion_text(avg_pace_mi_text),
        "Calories": notion_number(calories),
        "Activity Type": notion_select(activity_type),
        "Subactivity Type": notion_select(sub_activity_type),
        "Training Effect": notion_select(clean_training_label(training_effect_label)) if training_effect_label else None,
        "Aerobic": notion_number(ae_val),
        "Aerobic Effect": notion_select(clean_training_label(aerobic_msg)) if aerobic_msg else None,
        "Anaerobic": notion_number(an_val),
        "Anaerobic Effect": notion_select(clean_training_label(anaerobic_msg)) if anaerobic_msg else None,
        "AE:AN": notion_number(ratio_val)
    }
    return {k: v for k, v in props.items() if v is not None}

# ---------------------------
# Notion lookup & update helpers
# ---------------------------
def find_existing_activity_page(notion_client, db_id, act_name, act_date_iso, act_type):
    """Return page id if matching Activity Name + Date + Activity Type exists (date-only match)."""
    try:
        date_only = act_date_iso.split("T")[0]
        query = notion_client.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Activity Name", "title": {"equals": act_name}},
                    {"property": "Date", "date": {"equals": date_only}},
                    {"property": "Activity Type", "select": {"equals": act_type}},
                ]
            },
            page_size=1
        )
        results = query.get("results", [])
        if results:
            return results[0]["id"]
    except Exception as e:
        logger.warning(f"⚠️ Notion query error in find_existing_activity_page: {e}")
    return None

def get_latest_activity_date(notion_client, activities_db_id):
    """Return the most recent Date (as date object) recorded in the Activities DB"""
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
        start = props.get("Date", {}).get("date", {}).get("start")
        if not start:
            return None
        # parse date part
        dt = datetime.datetime.fromisoformat(start)
        return dt.date()
    except Exception as e:
        logger.warning(f"Could not query Notion for latest activity date: {e}")
        return None

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

    # ---------------------------
    # HEALTH METRICS (yesterday)
    # ---------------------------
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    logger.info(f"📅 Collecting Garmin health data for {yesterday.isoformat()}")
    steps = safe_fetch(garmin.get_daily_steps, yesterday.isoformat(), yesterday.isoformat()) or []
    sleep_data = safe_fetch(garmin.get_sleep_data, yesterday.isoformat()) or {}
    body_battery = safe_fetch(garmin.get_body_battery, yesterday.isoformat(), yesterday.isoformat()) or []
    body_comp = safe_fetch(garmin.get_body_composition, yesterday.isoformat()) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yesterday.isoformat()) or []
    status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or []
    stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

    # parse health values
    steps_total = sum(i.get("totalSteps", 0) for i in steps) if steps else None

    body_weight = None
    if isinstance(body_comp, dict) and body_comp.get("dateWeightList"):
        try:
            w = body_comp["dateWeightList"][0].get("weight")
            if w:
                body_weight = round(float(w) / 453.592, 2)
        except Exception:
            body_weight = None

    # sleep
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

    # training readiness/status
    training_readiness = extract_value(readiness, ["score", "trainingReadinessScore", "unknown_0"]) or None
    current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
    logger.info(f"Raw training status from Garmin: {current_status_val}")
    training_status_val = None
    if current_status_val is not None:
        # numeric codes
        try:
            if isinstance(current_status_val, (int, float, str)) and str(current_status_val).isdigit():
                training_status_val = TRAINING_STATUS_MAP.get(int(current_status_val))
            else:
                training_status_val = str(current_status_val)
        except Exception:
            training_status_val = str(current_status_val)

    # body battery min/max
    bb_min = bb_max = None
    if isinstance(body_battery, list) and body_battery:
        try:
            values = []
            for item in body_battery:
                # item can contain bodyBatteryValuesArray or values keyed by time
                arr = item.get("bodyBatteryValuesArray") or []
                if arr:
                    for v in arr:
                        if isinstance(v, (list, tuple)) and len(v) > 1 and v[1] is not None:
                            values.append(v[1])
                # maybe dict with numeric values
                for k, v in item.items():
                    if k != "bodyBatteryValuesArray" and isinstance(v, (int, float)):
                        values.append(v)
            if values:
                bb_min = min(values)
                bb_max = max(values)
        except Exception:
            bb_min = bb_max = None

    stats_obj = stats[0] if isinstance(stats, list) and stats else stats if isinstance(stats, dict) else {}
    calories = safe_fetch(lambda: extract_value(stats_obj, ["totalKilocalories", "active_calories"])) or None
    resting_hr = safe_fetch(lambda: extract_value(stats_obj, ["restingHeartRate", "heart_rate"])) or None

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
        calories
    )

    # push health metrics (only if required fields present)
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
    # ACTIVITIES
    # ---------------------------
    logger.info("Syncing activities...")

    # Fetch all existing Notion activity identifiers to prevent duplicates
    logger.info("Fetching existing activity records from Notion for duplicate check...")
    existing_records = {}
    try:
        has_more = True
        start_cursor = None
        while has_more:
            query = notion.databases.query(
                database_id=NOTION_ACTIVITIES_DB_ID,
                start_cursor=start_cursor,
                page_size=100
            )
            for r in query.get("results", []):
                props = r.get("properties", {})
                name = props.get("Activity Name", {}).get("title", [{}])[0].get("plain_text", "")
                date = props.get("Date", {}).get("date", {}).get("start", "")
                act_type = props.get("Activity Type", {}).get("select", {}).get("name", "")
                key = f"{name}|{date.split('T')[0]}|{act_type}"
                existing_records[key] = r["id"]
            has_more = query.get("has_more", False)
            start_cursor = query.get("next_cursor")
        logger.info(f"Loaded {len(existing_records)} existing activities from Notion.")
    except Exception as e:
        logger.warning(f"⚠️ Could not preload existing activities: {e}")
        existing_records = {}

    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Fetched {len(activities)} activities from Garmin")

    for act in activities:
        raw_ts = act.get("startTimeGMT") or act.get("startTimeLocal") or act.get("startTime")
        parsed_iso = parse_garmin_datetime(raw_ts)
        if not parsed_iso:
            continue

        try:
            dt_obj = datetime.datetime.fromisoformat(parsed_iso)
        except Exception:
            continue
        date_only = dt_obj.date().isoformat()

        activity_name = act.get("activityName") or f"Activity {date_only}"
        distance_m = act.get("distance")
        distance_km = float(distance_m) / 1000.0 if distance_m else None
        raw_dur = act.get("duration")
        duration_min = round(float(raw_dur) / 60.0, 2) if raw_dur else None
        avg_speed = act.get("averageSpeed")
        avg_pace_km_text, avg_pace_mi_text = compute_paces(avg_speed, duration_min, distance_km)
        calories = act.get("calories")
        raw_activity_type = act.get("activityType", {}) or ""
        act_type, subactivity = format_activity_type(raw_activity_type, activity_name)
        ae_effect = act.get("aerobicTrainingEffect") or extract_value(act, ["aeEffect"]) or None
        an_effect = act.get("anaerobicTrainingEffect") or extract_value(act, ["anEffect"]) or None
        training_effect_label = act.get("trainingEffectLabel")
        aerobic_msg = act.get("aerobicTrainingEffectMessage")
        anaerobic_msg = act.get("anaerobicTrainingEffectMessage")

        props = build_activity_properties(
            parsed_iso, activity_name, distance_km, duration_min,
            avg_pace_km_text, avg_pace_mi_text, calories, act_type,
            subactivity, ae_effect, an_effect, training_effect_label,
            aerobic_msg, anaerobic_msg
        )

        key = f"{activity_name}|{date_only}|{act_type}"
        page_id = existing_records.get(key)

        try:
            if page_id:
                notion.pages.update(page_id=page_id, properties=props)
                logger.info(f"🔁 Updated existing activity: {activity_name} ({date_only})")
            else:
                notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=props)
                logger.info(f"✅ Created new activity: {activity_name} ({date_only})")
        except Exception as e:
            logger.error(f"⚠️ Failed to push activity {activity_name}: {e}")
            logger.debug(pprint.pformat(props))

    try:
        garmin.logout()
    except Exception:
        pass

    logger.info("🏁 Sync complete.")

if __name__ == "__main__":
    main()
