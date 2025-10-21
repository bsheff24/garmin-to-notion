#!/usr/bin/env python3
"""
Garmin → Notion Sync
- Syncs Health Metrics (mock)
- Syncs Activities with icon logic, local time fix, rounding, and duplicate-safe updating
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
        logger.warning(f"⚠️ Garmin API error ({func.__name__}): {e}")
        return None


def to_local_iso(iso_str):
    """Convert Garmin UTC timestamp to local timezone ISO"""
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_local = dt.astimezone(LOCAL_TZ)
        return dt_local.isoformat()
    except Exception:
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
    if not value:
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}


def format_pace(average_speed):
    """Convert m/s to min/km string"""
    if not average_speed or average_speed <= 0:
        return "Unknown"
    pace_min_km = 1000 / (average_speed * 60)
    minutes = int(pace_min_km)
    seconds = int((pace_min_km - minutes) * 60)
    return f"{minutes}:{seconds:02d} min/km"


def format_activity_type(activity_type, activity_name=""):
    formatted = activity_type.replace("_", " ").title() if activity_type else "Unknown"
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
    if formatted in mapping:
        return mapping[formatted], formatted
    if "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return formatted, subtype


def format_training_effect(label):
    if not label:
        return None
    return label.replace("_", " ").title()


def format_training_message(message):
    if not message:
        return None
    mapping = {
        "NO_": "No Benefit",
        "MINOR_": "Some Benefit",
        "RECOVERY_": "Recovery",
        "MAINTAINING_": "Maintaining",
        "IMPROVING_": "Impacting",
        "IMPACTING_": "Impacting",
        "HIGHLY_": "Highly Impacting",
        "OVERREACHING_": "Overreaching"
    }
    for key, val in mapping.items():
        if message.startswith(key):
            return val
    return message


def build_activity_properties(act_iso, activity_name, distance_km, duration_min,
                              avg_pace_km_text, avg_pace_mi_text, calories,
                              activity_type, ae_effect, an_effect,
                              training_effect_label=None,
                              aerobic_msg=None, anaerobic_msg=None):
    ae_val = round(float(ae_effect), 1) if ae_effect else None
    an_val = round(float(an_effect), 1) if an_effect else None
    ratio = round(ae_val / an_val, 2) if (ae_val and an_val) else None
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


def find_existing_activity(notion, db_id, act_name, date_iso):
    """Return Notion page_id if activity with same name/date already exists"""
    try:
        results = notion.databases.query(
            **{
                "database_id": db_id,
                "filter": {
                    "and": [
                        {"property": "Activity Name", "title": {"equals": act_name}},
                        {"property": "Date", "date": {"equals": date_iso.split("T")[0]}},
                    ]
                },
            }
        )
        if results["results"]:
            return results["results"][0]["id"]
    except Exception as e:
        logger.warning(f"⚠️ Error searching existing activity: {e}")
    return None

# ---------------------------
# MAIN
# ---------------------------
def main():
    load_dotenv()

    if not (GARMIN_USERNAME and GARMIN_PASSWORD and NOTION_TOKEN and NOTION_ACTIVITIES_DB_ID):
        logger.error("Missing required environment variables.")
        return

    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    logger.info("🔑 Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logger.error(f"Failed Garmin login: {e}")
        return

    logger.info("📡 Fetching Garmin activities...")
    activities = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Found {len(activities)} activities total.")

    for act in activities:
        act_iso_local = to_local_iso(act.get("startTimeGMT"))
        act_name = act.get("activityName", "Unnamed Activity")
        distance_km = (act.get("distance") or 0) / 1000
        duration_min = (act.get("duration") or 0) / 60
        avg_speed = act.get("averageSpeed", 0)
        avg_pace_km = format_pace(avg_speed)
        avg_pace_mi = "Unknown"
        calories = act.get("calories", 0)
        raw_type = act.get("activityType", {}).get("typeKey", "")
        act_type, _ = format_activity_type(raw_type, act_name)
        ae = act.get("aerobicTrainingEffect", 0)
        an = act.get("anaerobicTrainingEffect", 0)
        training_label = format_training_effect(act.get("trainingEffectLabel"))
        aerobic_msg = format_training_message(act.get("aerobicTrainingEffectMessage"))
        anaerobic_msg = format_training_message(act.get("anaerobicTrainingEffectMessage"))

        props = build_activity_properties(
            act_iso_local, act_name, distance_km, duration_min,
            avg_pace_km, avg_pace_mi, calories,
            act_type, ae, an, training_label, aerobic_msg, anaerobic_msg
        )

        page_id = find_existing_activity(notion, NOTION_ACTIVITIES_DB_ID, act_name, act_iso_local)

        icon_url = ACTIVITY_ICONS.get(act_type)
        page_data = {"parent": {"database_id": NOTION_ACTIVITIES_DB_ID}, "properties": props}
        if icon_url:
            page_data["icon"] = {"type": "external", "external": {"url": icon_url}}

        try:
            if page_id:
                notion.pages.update(page_id=page_id, properties=props)
                logger.info(f"🔁 Updated existing activity: {act_name}")
            else:
                notion.pages.create(**page_data)
                logger.info(f"✅ Created new activity: {act_name}")
        except Exception as e:
            logger.warning(f"❌ Failed to push activity {act_name}: {e}")


if __name__ == "__main__":
    main()
