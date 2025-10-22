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
    0: "No Status",
    1: "Detraining",
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
    6: "Unproductive",
    7: "Overreaching",
    8: "Strained",
    9: "Paused"
}
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
    if not dt_str:
        return None
    s = str(dt_str).strip()
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        if "T" in s and ("+" in s[19:] or "-" in s[19:]):
            dt = datetime.datetime.fromisoformat(s)
            return dt.astimezone(LOCAL_TZ).isoformat()
        try:
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo:
                return dt.astimezone(LOCAL_TZ).isoformat()
        except Exception:
            pass
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
# ACTIVITY HELPERS
# ---------------------------
def format_activity_type(activity_type, activity_name=""):
    if isinstance(activity_type, dict):
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
    if activity_name and "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if activity_name and "barre" in activity_name.lower():
        return "Strength", "Barre"
    if activity_name and "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return main, subtype

def compute_paces(average_speed_mps, duration_min, distance_km):
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
    return "Unknown", "Unknown"

# ---------------------------
# HEALTH + ACTIVITY BUILDERS
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
    return {k: v for k, v in props.items() if v is not None}

# (activity build + Notion helpers remain unchanged)
# ...


# ---------------------------
# Notion update helpers
# ---------------------------
def find_existing_activity_page(notion_client, db_id, act_name, act_date_iso, act_type):
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
        logger.warning(f"⚠️ Notion query error: {e}")
    return None

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
        start = results[0]["properties"].get("Date", {}).get("date", {}).get("start")
        if start:
            return datetime.datetime.fromisoformat(start).date()
    except Exception as e:
        logger.warning(f"Could not query latest date: {e}")
    return None

# ---------------------------
# MAIN
# ---------------------------
def main():
    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    garmin.login()

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    logger.info(f"📅 Collecting Garmin health data for {yesterday.isoformat()}")

    steps = safe_fetch(garmin.get_daily_steps, yesterday.isoformat(), yesterday.isoformat()) or []
    sleep = safe_fetch(garmin.get_sleep_data, yesterday.isoformat()) or {}
    bb = safe_fetch(garmin.get_body_battery, yesterday.isoformat(), yesterday.isoformat()) or []
    body = safe_fetch(garmin.get_body_composition, yesterday.isoformat()) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yesterday.isoformat()) or []
    status = safe_fetch(garmin.get_training_status, yesterday.isoformat()) or []
    stats = safe_fetch(garmin.get_stats_and_body, yesterday.isoformat()) or []

    steps_total = sum(i.get("totalSteps", 0) for i in steps) if steps else None

    body_weight = None
    if isinstance(body, dict) and body.get("dateWeightList"):
        try:
            w = body["dateWeightList"][0].get("weight")
            if w:
                body_weight = round(float(w) / 453.592, 2)
        except Exception:
            pass

    sleep_daily = sleep.get("dailySleepDTO", {})
    sleep_score = extract_value(sleep_daily, ["sleepScores", "overall", "value"])
    bed_ts = sleep_daily.get("sleepStartTimestampGMT")
    wake_ts = sleep_daily.get("sleepEndTimestampGMT")

    bed_iso = datetime.datetime.fromtimestamp(bed_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if bed_ts else None
    wake_iso = datetime.datetime.fromtimestamp(wake_ts / 1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ).isoformat() if wake_ts else None

    training_readiness = extract_value(readiness, ["score", "trainingReadinessScore"])

    # 🧠 Updated Training Status logic
    logger.debug(f"Raw training status response: {status}")
    possible_keys = [
        "currentStatus", "trainingStatus", "status",
        "currentTrainingStatus", "userTrainingStatus",
        "trainingStatusValue", "primaryTrainingStatus"
    ]
    current_status_val = extract_value(status, possible_keys)
    logger.info(f"Raw training status from Garmin (parsed): {current_status_val}")

    training_status_val = None
    if current_status_val is not None:
        try:
            if isinstance(current_status_val, (int, float)) or str(current_status_val).isdigit():
                training_status_val = TRAINING_STATUS_MAP.get(int(current_status_val), f"Code {current_status_val}")
            else:
                training_status_val = str(current_status_val).replace("_", " ").title()
        except Exception:
            training_status_val = str(current_status_val)

    bb_min = bb_max = None
    if isinstance(bb, list) and bb:
        try:
            vals = []
            for item in bb:
                arr = item.get("bodyBatteryValuesArray") or []
                for v in arr:
                    if isinstance(v, (list, tuple)) and len(v) > 1:
                        vals.append(v[1])
            if vals:
                bb_min, bb_max = min(vals), max(vals)
        except Exception:
            pass

    stats_obj = stats[0] if isinstance(stats, list) and stats else stats
    calories = extract_value(stats_obj, ["totalKilocalories", "active_calories"])
    resting_hr = extract_value(stats_obj, ["restingHeartRate", "heart_rate"])

    health_props = build_health_properties(
        yesterday, steps_total, body_weight, bb_min, bb_max,
        sleep_score, bed_iso, wake_iso,
        training_readiness, training_status_val,
        resting_hr, calories
    )

    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
    logger.info("✅ Synced health metrics (yesterday)")

    # Activities sync stays the same
    logger.info("🏁 Sync complete.")
    garmin.logout()

if __name__ == "__main__":
    main()

