#!/usr/bin/env python3
"""
Garmin → Notion Sync (v3)
Fixes:
- Icons now appear correctly for new activities
- Duplicate detection now uses Activity Name + Date + Activity Type
- Keeps Subactivity + timezone + rounding improvements
"""

import os
import datetime
import logging
from notion_client import Client
from garminconnect import Garmin
import pytz
from dotenv import load_dotenv

DEBUG = True
LOCAL_TZ = pytz.timezone("America/Chicago")
GARMIN_ACTIVITY_FETCH_LIMIT = 200

load_dotenv()
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("garmin_to_notion")
if DEBUG:
    logger.setLevel(logging.DEBUG)

ACTIVITY_ICONS = {
    "Running": "https://img.icons8.com/?size=100&id=k1l1XFkME39t&format=png&color=000000",
    "Walking": "https://img.icons8.com/?size=100&id=9807&format=png&color=000000",
    "Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Swimming": "https://img.icons8.com/?size=100&id=9777&format=png&color=000000",
    "Strength Training": "https://img.icons8.com/?size=100&id=107640&format=png&color=000000",
    "Stretching": "https://img.icons8.com/?size=100&id=djfOcRn1m_kh&format=png&color=000000",
    "Yoga": "https://img.icons8.com/?size=100&id=9783&format=png&color=000000",
    "Meditation": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Pilates": "https://img.icons8.com/?size=100&id=9774&format=png&color=000000",
    "Hiking": "https://img.icons8.com/?size=100&id=9844&format=png&color=000000",
    "Indoor Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Indoor Rowing": "https://img.icons8.com/?size=100&id=71098&format=png&color=000000",
    "Treadmill Running": "https://img.icons8.com/?size=100&id=9794&format=png&color=000000",
    "Barre": "https://img.icons8.com/?size=100&id=66924&format=png&color=000000",
}

def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"⚠️ Garmin API error ({getattr(func,'__name__',func)}): {e}")
        return None

def to_local_iso(gmt_string):
    if not gmt_string:
        return None
    s = gmt_string.strip()
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
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]
        for f in fmts:
            try:
                dt = datetime.datetime.strptime(s, f)
                dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
                return dt.isoformat()
            except Exception:
                continue
        dt = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(LOCAL_TZ)
        return dt.isoformat()
    except Exception as e:
        logger.debug(f"to_local_iso parse error for '{gmt_string}': {e}")
        return None

def notion_date_obj_from_iso(iso_str):
    return {"date": {"start": iso_str}} if iso_str else None

def notion_number(value):
    if value is None:
        return None
    try:
        return {"number": round(float(value), 2)}
    except Exception:
        return None

def notion_select(name):
    return {"select": {"name": str(name)}} if name else None

def notion_title(text):
    return {"title": [{"text": {"content": str(text)}}]}

def notion_text(value):
    return {"rich_text": [{"text": {"content": str(value)}}]} if value else None

def format_pace_m_per_s_to_min_km(speed_mps):
    if not speed_mps or speed_mps <= 0:
        return None
    pace_min_km = 1000 / (speed_mps * 60)
    minutes, seconds = divmod(int(round(pace_min_km * 60)), 60)
    return f"{minutes}:{seconds:02d} min/km"

def format_pace_m_per_s_to_min_mi(speed_mps):
    if not speed_mps or speed_mps <= 0:
        return None
    pace_min_mi = 1609.34 / (speed_mps * 60)
    minutes, seconds = divmod(int(round(pace_min_mi * 60)), 60)
    return f"{minutes}:{seconds:02d} min/mi"

def format_activity_type(activity_type, activity_name=""):
    formatted_type = activity_type.replace("_", " ").title() if activity_type else "Unknown"
    activity_subtype = formatted_type
    mapping = {
        "Barre": "Strength Training",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength Training",
        "Treadmill Running": "Running",
    }
    if formatted_type in mapping:
        activity_type = mapping[formatted_type]
        activity_subtype = formatted_type
    if "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if "barre" in activity_name.lower():
        return "Strength Training", "Barre"
    if "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return activity_type, activity_subtype

def build_activity_properties(act_iso, act_name, dist_km, dur_min, avg_km, avg_mi, cal,
                              act_type, ae, an, label, ae_msg, an_msg, subactivity):
    ae_val = round(float(ae), 1) if ae else None
    an_val = round(float(an), 1) if an else None
    ratio = round(ae_val / an_val, 2) if (ae_val and an_val and an_val != 0) else None
    props = {
        "Activity Name": notion_title(act_name),
        "Date": notion_date_obj_from_iso(act_iso),
        "Distance (km)": notion_number(dist_km),
        "Distance (mi)": notion_number(dist_km * 0.621371 if dist_km else None),
        "Duration (mins)": notion_number(dur_min),
        "Avg Pace (min/km)": notion_text(avg_km),
        "Avg Pace (min/mi)": notion_text(avg_mi),
        "Calories": notion_number(cal),
        "Activity Type": notion_select(act_type),
        "Subactivity Type": notion_select(subactivity),
        "Training Effect": notion_select(label),
        "Aerobic": notion_number(ae_val),
        "Aerobic Effect": notion_select(ae_msg),
        "Anaerobic": notion_number(an_val),
        "Anaerobic Effect": notion_select(an_msg),
        "AE:AN": notion_number(ratio),
    }
    return {k: v for k, v in props.items() if v is not None}

def find_existing_activity(notion, db_id, act_name, date_iso, act_type):
    """Find an existing page by name + date + type."""
    try:
        date_only = date_iso.split("T")[0]
        results = notion.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Activity Name", "title": {"equals": act_name}},
                    {"property": "Date", "date": {"equals": date_only}},
                    {"property": "Activity Type", "select": {"equals": act_type}},
                ]
            },
        )
        if results.get("results"):
            return results["results"][0]["id"]
    except Exception as e:
        logger.warning(f"⚠️ Search error: {e}")
    return None

def main():
    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    logger.info("Logging into Garmin...")
    garmin.login()
    acts = safe_fetch(garmin.get_activities, 0, GARMIN_ACTIVITY_FETCH_LIMIT) or []
    logger.info(f"Found {len(acts)} activities total.")

    for act in acts:
        act_iso = to_local_iso(act.get("startTimeGMT") or act.get("startTimeLocal") or act.get("startTime"))
        if not act_iso:
            continue
        name = act.get("activityName", "Unnamed Activity")
        raw_type = act.get("activityType", {}).get("typeKey", "")
        act_type, subactivity = format_activity_type(raw_type, name)
        dist_km = round((act.get("distance") or 0) / 1000, 2)
        dur_min = round((act.get("duration") or 0) / 60, 2)
        avg_km = format_pace_m_per_s_to_min_km(act.get("averageSpeed"))
        avg_mi = format_pace_m_per_s_to_min_mi(act.get("averageSpeed"))
        cal = act.get("calories") or 0
        ae, an = act.get("aerobicTrainingEffect"), act.get("anaerobicTrainingEffect")
        label = act.get("trainingEffectLabel")
        ae_msg, an_msg = act.get("aerobicTrainingEffectMessage"), act.get("anaerobicTrainingEffectMessage")

        props = build_activity_properties(act_iso, name, dist_km, dur_min, avg_km, avg_mi,
                                          cal, act_type, ae, an, label, ae_msg, an_msg, subactivity)
        page_id = find_existing_activity(notion, NOTION_ACTIVITIES_DB_ID, name, act_iso, act_type)
        icon_url = ACTIVITY_ICONS.get(act_type)

        page_payload = {
            "parent": {"database_id": NOTION_ACTIVITIES_DB_ID},
            "properties": props,
        }

        if icon_url:
            page_payload["icon"] = {"type": "external", "external": {"url": icon_url}}
        else:
            page_payload["icon"] = {"type": "emoji", "emoji": "🏃"}

        try:
            if page_id:
                notion.pages.update(page_id=page_id, properties=props)
                logger.info(f"🔁 Updated: {name} ({act_type})")
            else:
                notion.pages.create(**page_payload)
                logger.info(f"✅ Created: {name} ({act_type})")
        except Exception as e:
            logger.warning(f"❌ Error pushing '{name}': {e}")

if __name__ == "__main__":
    main()
