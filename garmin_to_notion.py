#!/usr/bin/env python3
"""
Unified Garmin → Notion sync script
- Health metrics: pushes yesterday's metrics to Health Metrics DB (title property "Name")
- Activities: pushes new activities with full properties, rounded numerics, icons, and formatted strings
- Adds duplicate prevention, consistent Avg Pace formatting, and icon handling
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
LOCAL_TZ = pytz.timezone("America/Chicago")
GARMIN_ACTIVITY_FETCH_LIMIT = 200

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
logger = logging.getLogger("garmin_to_notion")
if DEBUG:
    logger.setLevel(logging.DEBUG)

# ---------------------------
# ICONS
# ---------------------------
ACTIVITY_ICONS = {
    "Barre": "https://img.icons8.com/?size=100&id=66924&format=png&color=000000",
    "Breathwork": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Cardio": "https://img.icons8.com/?size=100&id=71221&format=png&color=000000",
    "Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Hiking": "https://img.icons8.com/?size=100&id=9844&format=png&color=000000",
    "Indoor Cardio": "https://img.icons8.com/?size=100&id=62779&format=png&color=000000",
    "Indoor Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Indoor Rowing": "https://img.icons8.com/?size=100&id=71098&format=png&color=000000",
    "Pilates": "https://img.icons8.com/?size=100&id=9774&format=png&color=000000",
    "Meditation": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Rowing": "https://img.icons8.com/?size=100&id=71491&format=png&color=000000",
    "Running": "https://img.icons8.com/?size=100&id=k1l1XFkME39t&format=png&color=000000",
    "Strength Training": "https://img.icons8.com/?size=100&id=107640&format=png&color=000000",
    "Stretching": "https://img.icons8.com/?size=100&id=djfOcRn1m_kh&format=png&color=000000",
    "Swimming": "https://img.icons8.com/?size=100&id=9777&format=png&color=000000",
    "Treadmill Running": "https://img.icons8.com/?size=100&id=9794&format=png&color=000000",
    "Walking": "https://img.icons8.com/?size=100&id=9807&format=png&color=000000",
    "Yoga": "https://img.icons8.com/?size=100&id=9783&format=png&color=000000",
}

# ---------------------------
# HELPERS
# ---------------------------
def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"⚠️ Error fetching from Garmin API ({func.__name__}): {e}")
        return None

def notion_date_obj_from_iso(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        dt = dt.astimezone(LOCAL_TZ)
        return {"date": {"start": dt.isoformat()}}
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
    if name is None:
        return None
    return {"select": {"name": str(name)}}

def notion_title(text):
    return {"title": [{"text": {"content": str(text)}}]}

def notion_text(value):
    if value is None or value == "":
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}

# ---------------------------
# ACTIVITY HELPERS
# ---------------------------
def format_activity_type(activity_type, activity_name=""):
    formatted_type = activity_type.replace('_', ' ').title() if activity_type else "Unknown"
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
    if average_speed > 0:
        pace_min_km = 1000 / (average_speed * 60)
        minutes = int(pace_min_km)
        seconds = int((pace_min_km - minutes) * 60)
        return f"{minutes}:{seconds:02d} min/km"
    return "Unknown"

def format_training_effect(label):
    if not label:
        return None
    return label.replace("_", " ").title()

def format_training_message(message):
    if not message:
        return None
    messages = {
        'NO_': 'No Benefit',
        'MINOR_': 'Some Benefit',
        'RECOVERY_': 'Recovery',
        'MAINTAINING_': 'Maintaining',
        'IMPROVING_': 'Impacting',
        'IMPACTING_': 'Impacting',
        'HIGHLY_': 'Highly Impacting',
        'OVERREACHING_': 'Overreaching'
    }
    for key, value in messages.items():
        if message.startswith(key):
            return value
    return message

# ---------------------------
# BUILD ACTIVITY PROPERTIES
# ---------------------------
def build_activity_properties(act_iso, activity_name, distance_km, duration_min,
                              avg_pace_km_text, avg_pace_mi_text, calories,
                              activity_type, ae_effect, an_effect, training_effect_label=None,
                              aerobic_msg=None, anaerobic_msg=None):
    ae_val = round(float(ae_effect), 1) if ae_effect is not None else None
    an_val = round(float(an_effect), 1) if an_effect is not None else None
    ratio = round(ae_val / an_val, 2) if (ae_val and an_val and an_val != 0) else None
    distance_km_rounded = round(distance_km, 2) if distance_km is not None else None
    distance_mi_rounded = round(distance_km * 0.621371, 2) if distance_km is not None else None
    duration_rounded = round(duration_min, 2) if duration_min is not None else None

    props = {
        "Activity Name": notion_title(activity_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(distance_km_rounded),
        "Distance (mi)": notion_number(distance_mi_rounded),
        "Duration (mins)": notion_number(duration_rounded),
        "Avg Pace (min/km)": notion_text(avg_pace_km_text),
        "Avg Pace (min/mi)": notion_text(avg_pace_mi_text),
        "Calories": notion_number(calories),
        "Activity Type": notion_select(activity_type),
        "Training Effect": notion_select(training_effect_label),
        "Aerobic": notion_number(ae_val),
        "Aerobic Effect": notion_select(aerobic_msg),
        "Anaerobic": notion_number(an_val),
        "Anaerobic Effect": notion_select(anaerobic_msg),
        "AE:AN": notion_number(ratio)
    }
    return {k: v for k, v in props.items() if v is not None}

# ---------------------------
# DUPLICATE CHECK
# ---------------------------
def activity_exists(client, database_id, act_date, act_name):
    query = client.databases.query(
        database_id=database_id,
        filter={
            "and": [
                {"property": "Date", "date": {"equals": act_date.split("T")[0]}},
                {"property": "Activity Name", "title": {"equals": act_name}}
            ]
        }
    )
    return len(query.get("results", [])) > 0

# ---------------------------
# MAIN
# ---------------------------
def main():
    load_dotenv()

    if not (GARMIN_USERNAME and GARMIN_PASSWORD and NOTION_TOKEN and NOTION_ACTIVITIES_DB_ID):
        logger.error("❌ Missing required environment variables.")
        return

    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    logger.info("Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logger.error(f"Failed to login to Garmin: {e}")
        return

    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Found {len(activities)} Garmin activities.")

    for act in activities:
        act_iso = act.get("startTimeGMT")
        act_name = act.get("activityName", "Unnamed Activity")
        distance_km = act.get("distance", 0) / 1000
        duration_min = act.get("duration", 0) / 60
        avg_speed = act.get("averageSpeed", 0)
        avg_pace_km = format_pace(avg_speed)
        avg_pace_mi = ""  # optional
        calories = act.get("calories", 0)
        act_type, _ = format_activity_type(act.get("activityType", {}).get("typeKey", ""), act_name)
        ae = act.get("aerobicTrainingEffect", 0)
        an = act.get("anaerobicTrainingEffect", 0)
        training_effect_label = format_training_effect(act.get("trainingEffectLabel"))
        aerobic_msg = format_training_message(act.get("aerobicTrainingEffectMessage"))
        anaerobic_msg = format_training_message(act.get("anaerobicTrainingEffectMessage"))

        # Skip if activity already exists
        if activity_exists(notion, NOTION_ACTIVITIES_DB_ID, act_iso, act_name):
            logger.debug(f"⏭ Skipping existing activity: {act_name}")
            continue

        props = build_activity_properties(
            act_iso, act_name, distance_km, duration_min,
            avg_pace_km, avg_pace_mi, calories,
            act_type, ae, an, training_effect_label, aerobic_msg, anaerobic_msg
        )

        icon_url = ACTIVITY_ICONS.get(act_type)
        page_data = {"parent": {"database_id": NOTION_ACTIVITIES_DB_ID}, "properties": props}
        if icon_url:
            page_data["icon"] = {"type": "external", "external": {"url": icon_url}}

        try:
            notion.pages.create(**page_data)
            logger.info(f"✅ Created: {act_name}")
        except Exception as e:
            logger.warning(f"❌ Failed to create {act_name}: {e}")

if __name__ == "__main__":
    main()
