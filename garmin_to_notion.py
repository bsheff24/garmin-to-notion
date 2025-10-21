#!/usr/bin/env python3
"""
Garmin → Notion Sync Script (final version for Bryson)
- Pushes Garmin activities with cleaned labels, rounded numerics, correct local times
- Keeps Notion templates/icons intact
"""

import os
import datetime
import logging
from notion_client import Client
from garminconnect import Garmin
import pytz
from dotenv import load_dotenv

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = pytz.timezone("America/Chicago")  # change if needed
GARMIN_ACTIVITY_FETCH_LIMIT = 200

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
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
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"⚠️ Garmin API error in {func.__name__}: {e}")
        return None


def notion_date_obj_from_iso(iso_str):
    """Convert Garmin ISO UTC to Notion date in local time"""
    if not iso_str:
        return None
    try:
        utc_dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = utc_dt.astimezone(LOCAL_TZ)
        return {"date": {"start": local_dt.isoformat()}}
    except Exception:
        return None


def notion_number(value):
    if value is None:
        return None
    try:
        v = float(value)
        if v == 0:
            return None
        return {"number": v}
    except Exception:
        return None


def notion_select(name):
    if not name:
        return None
    return {"select": {"name": str(name)}}


def notion_title(text):
    return {"title": [{"text": {"content": str(text)}}]}


def notion_text(value):
    if not value:
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}


def extract_value(data, keys):
    """Recursively search nested dict for a key"""
    if not data:
        return None
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                return data[k]
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
# TRAINING LABEL CLEANUP
# ---------------------------
def clean_training_label(label):
    """Simplify ugly Garmin labels like IMPROVING_VO2_MAX_15 → Improving"""
    if not label:
        return None
    label = label.upper()
    mappings = {
        "IMPROVING": "Improving",
        "HIGHLY_IMPACTING": "Highly Impacting",
        "IMPACTING": "Impacting",
        "MAINTAINING": "Maintaining",
        "RECOVERY": "Recovery",
        "NO_AEROBIC_BENEFIT": "No Benefit",
        "NO_BENEFIT": "No Benefit",
        "MINOR": "Some Benefit",
        "OVERREACHING": "Overreaching",
        "HIGHLY": "Highly Impacting",
    }
    for key, value in mappings.items():
        if key in label:
            return value
    return label.title()


# ---------------------------
# ACTIVITY FORMAT HELPERS
# ---------------------------
def format_activity_type(activity_type, activity_name=""):
    formatted_type = activity_type.replace("_", " ").title() if activity_type else "Unknown"
    activity_subtype = formatted_type
    activity_mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running"
    }
    if formatted_type in activity_mapping:
        activity_type = activity_mapping[formatted_type]
        activity_subtype = formatted_type
    if activity_name and "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if activity_name and "barre" in activity_name.lower():
        return "Strength", "Barre"
    if activity_name and "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return activity_type, activity_subtype


def format_pace(average_speed):
    if average_speed and average_speed > 0:
        pace_min_km = 1000 / (average_speed * 60)
        minutes = int(pace_min_km)
        seconds = int((pace_min_km - minutes) * 60)
        return f"{minutes}:{seconds:02d} min/km"
    return ""


# ---------------------------
# BUILD ACTIVITY PROPERTIES
# ---------------------------
def build_activity_properties(act_iso, activity_name, distance_km, duration_min,
                              avg_pace_km_text, calories,
                              activity_type, sub_activity_type,
                              ae_effect, an_effect, training_effect_label,
                              aerobic_msg, anaerobic_msg):
    ae_val = round(float(ae_effect), 1) if ae_effect is not None else None
    an_val = round(float(an_effect), 1) if an_effect is not None else None
    ratio = round(ae_val / an_val, 2) if (ae_val and an_val and an_val != 0) else None
    distance_km_rounded = round(distance_km, 2) if distance_km else None
    distance_mi_rounded = round(distance_km * 0.621371, 2) if distance_km else None
    duration_rounded = round(duration_min, 2) if duration_min else None

    props = {
        "Activity Name": notion_title(activity_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(distance_km_rounded),
        "Distance (mi)": notion_number(distance_mi_rounded),
        "Duration (mins)": notion_number(duration_rounded),
        "Avg Pace (min/km)": notion_text(avg_pace_km_text),
        "Calories": notion_number(calories),
        "Activity Type": notion_select(activity_type),
        "Sub Activity Type": notion_select(sub_activity_type),
        "Training Effect": notion_select(clean_training_label(training_effect_label)),
        "Aerobic": notion_number(ae_val),
        "Aerobic Effect": notion_select(clean_training_label(aerobic_msg)),
        "Anaerobic": notion_number(an_val),
        "Anaerobic Effect": notion_select(clean_training_label(anaerobic_msg)),
        "AE:AN": notion_number(ratio)
    }
    return {k: v for k, v in props.items() if v is not None}


# ---------------------------
# MAIN
# ---------------------------
def main():
    load_dotenv()
    if not all([GARMIN_USERNAME, GARMIN_PASSWORD, NOTION_TOKEN, NOTION_ACTIVITIES_DB_ID]):
        logger.error("❌ Missing required environment variables")
        return

    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    logger.info("🔐 Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logger.error(f"Failed Garmin login: {e}")
        return

    logger.info("📥 Fetching Garmin activities...")
    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Found {len(activities)} activities")

    for act in activities:
        act_iso = act.get("startTimeGMT")
        act_name = act.get("activityName", "Unnamed Activity")
        distance_km = act.get("distance", 0) / 1000
        duration_min = act.get("duration", 0) / 60
        avg_speed = act.get("averageSpeed", 0)
        avg_pace_km = format_pace(avg_speed)
        calories = act.get("calories", 0)
        raw_type = extract_value(act, ["activityType", "typeKey"])
        act_type, sub_act_type = format_activity_type(raw_type, act_name)

        ae = act.get("aerobicTrainingEffect", 0)
        an = act.get("anaerobicTrainingEffect", 0)
        training_effect_label = act.get("trainingEffectLabel")
        aerobic_msg = act.get("aerobicTrainingEffectMessage")
        anaerobic_msg = act.get("anaerobicTrainingEffectMessage")

        props = build_activity_properties(
            act_iso, act_name, distance_km, duration_min,
            avg_pace_km, calories,
            act_type, sub_act_type,
            ae, an, training_effect_label,
            aerobic_msg, anaerobic_msg
        )

        page_data = {"parent": {"database_id": NOTION_ACTIVITIES_DB_ID}, "properties": props}

        try:
            notion.pages.create(**page_data)
            logger.info(f"✅ Synced: {act_name}")
        except Exception as e:
            logger.warning(f"❌ Failed to push {act_name}: {e}")

    logger.info("🎉 Sync complete.")


if __name__ == "__main__":
    main()
