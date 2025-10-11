#!/usr/bin/env python3
"""
Final Garmin → Notion Sync Script
- Robust property mapping (handles title vs rich_text)
- Garmin data normalization
- Converts weight to lbs
- Automatically cleans up test row
- Detailed debug logging for first run
"""

import os
import sys
import datetime
import logging
from typing import Any, Dict, Optional
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---------------------------
# CLIENTS
# ---------------------------
notion = Client(auth=NOTION_TOKEN)
garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)

# ---------------------------
# HELPERS
# ---------------------------
def to_iso(ts: Optional[Any]) -> Optional[str]:
    if not ts:
        return None
    try:
        t = float(ts)
        if t > 1e12:
            t /= 1000
        return datetime.datetime.utcfromtimestamp(t).isoformat() + "Z"
    except Exception:
        if isinstance(ts, str) and "T" in ts:
            return ts
        return None

def normalize_name(name: str) -> str:
    return "".join(name.lower().split())

def find_property_key(db_props: Dict[str, Any], desired_name: str) -> Optional[str]:
    norm = normalize_name(desired_name)
    for key in db_props.keys():
        if normalize_name(key) == norm:
            return key
    for key in db_props.keys():
        if norm in normalize_name(key):
            return key
    return None

def build_property(prop_type: str, value: Any):
    if value is None:
        return {prop_type: None} if prop_type in ("number", "date", "url") else {prop_type: []}
    if prop_type == "number":
        return {"number": float(value)}
    if prop_type == "date":
        iso = to_iso(value)
        return {"date": {"start": iso}} if iso else {"date": None}
    if prop_type == "title":
        return {"title": [{"text": {"content": str(value)}}]}
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}
    if prop_type == "select":
        return {"select": {"name": str(value)}}
    return {"rich_text": [{"text": {"content": str(value)}}]}

def map_props(db_id: str, desired_map: Dict[str, Any]) -> Dict[str, Any]:
    db = notion.databases.retrieve(db_id)
    props = db.get("properties", {})
    result = {}
    for friendly, value in desired_map.items():
        prop_key = find_property_key(props, friendly)
        if not prop_key:
            logging.warning("⚠️ Property '%s' not found in Notion DB.", friendly)
            continue
        prop_type = props[prop_key]["type"]
        result[prop_key] = build_property(prop_type, value)
    return result

def safe_fetch(func, *args):
    try:
        return func(*args)
    except Exception as e:
        logging.warning("Garmin fetch %s failed: %s", func.__name__, e)
        return None

def safe_extract(data, *keys):
    for k in keys:
        if isinstance(data, dict) and k in data:
            return data[k]
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        for k in keys:
            if k in data[0]:
                return data[0][k]
    return None

# ---------------------------
# MAIN
# ---------------------------
def main():
    logging.info("🔐 Logging into Garmin...")
    garmin.login()

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    yday_str = yesterday.isoformat()
    logging.info(f"📅 Syncing data for {yday_str}")

    # Garmin fetch
    activities = safe_fetch(garmin.get_activities, 0, 10) or []
    steps = safe_fetch(garmin.get_daily_steps, yday_str, yday_str) or []
    sleep = safe_fetch(garmin.get_sleep_data, yday_str) or {}
    battery = safe_fetch(garmin.get_body_battery, yday_str, yday_str) or []
    comp = safe_fetch(garmin.get_body_composition, yday_str) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yday_str) or {}
    status = safe_fetch(garmin.get_training_status, yday_str) or {}
    stats = safe_fetch(garmin.get_stats_and_body, yday_str) or {}

    # Sleep
    sleep_daily = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
    sleep_score = (
        safe_extract(sleep_daily, "sleepScore") or
        safe_extract(sleep_daily.get("sleepScores") or {}, "overallScore")
    )
    bed_time = safe_extract(sleep_daily, "sleepStartTimestampGMT", "sleepStartTimeInSeconds")
    wake_time = safe_extract(sleep_daily, "sleepEndTimestampGMT", "sleepEndTimeInSeconds")

    # Body battery
    body_battery_value = None
    if isinstance(battery, list) and battery:
        body_battery_value = (
            battery[-1].get("bodyBatteryValue") or
            battery[-1].get("bodyBatteryHighestValue")
        )

    # Body weight → lbs
    body_weight = None
    if comp.get("dateWeightList"):
        raw = comp["dateWeightList"][0].get("weight")
        if raw:
            body_weight = round(float(raw) / 453.592, 1)  # grams → lbs

    # Readiness + training
    training_readiness = safe_extract(readiness, "score")
    training_status_val = (
        safe_extract(status, "trainingStatus") or
        safe_extract(status.get("trainingStatus") or {}, "trainingStatus")
    )

    # Stats
    resting_hr = safe_extract(stats, "restingHeartRate")
    stress = (
        safe_extract(stats, "stressLevelAvg", "stressScore", "overallStressLevel")
    )
    calories = safe_extract(stats, "totalKilocalories")

    # Steps
    steps_total = 0
    if isinstance(steps, list):
        steps_total = sum(i.get("totalSteps", 0) for i in steps)

    # Build health record
    health_map = {
        "Date": yday_str,
        "Steps": steps_total,
        "Body Weight": body_weight,
        "Body Battery": body_battery_value,
        "Sleep Score": sleep_score,
        "Bedtime": bed_time,
        "Wake Time": wake_time,
        "Training Readiness": training_readiness,
        "Training Status": training_status_val,
        "Resting HR": resting_hr,
        "Stress": stress,
        "Calories Burned": calories,
    }

    # --- Test push (deleted after success)
    logging.info("🧪 Verifying Notion connection with test row...")
    try:
        test_payload = map_props(NOTION_HEALTH_DB_ID, {"Date": yday_str, "Steps": 42})
        test_page = notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=test_payload)
        if test_page.get("id"):
            notion.blocks.delete(test_page["id"])
            logging.info("✅ Notion connection verified and test row removed.")
    except Exception as e:
        logging.error("❌ Notion test push failed: %s", e)
        garmin.logout()
        sys.exit(1)

    # --- Push health data
    logging.info("📤 Pushing Garmin health metrics to Notion...")
    props = map_props(NOTION_HEALTH_DB_ID, health_map)
    try:
        notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=props)
        logging.info(f"✅ Added health metrics for {yday_str}")
    except Exception as e:
        logging.error("⚠️ Failed to push health data: %s", e)

    # --- Push activities
    logging.info(f"📤 Syncing {len(activities)} activities...")
    for act in activities:
        act_date = act.get("startTimeLocal", "")[:10] or yday_str
        act_map = {
            "Date": act_date,
            "Activity Name": act.get("activityName"),
            "Distance (km)": (act.get("distance") or 0) / 1000,
            "Calories": act.get("calories"),
            "Duration (min)": round((act.get("duration") or 0) / 60, 1),
            "Type": act.get("activityType", {}).get("typeKey"),
        }
        props = map_props(NOTION_ACTIVITIES_DB_ID, act_map)
        try:
            notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=props)
            logging.info(f"🏃 Logged activity: {act.get('activityName')}")
        except Exception as e:
            logging.error("⚠️ Failed to log %s: %s", act.get("activityName"), e)

    garmin.logout()
    logging.info("🏁 Sync complete.")
    

if __name__ == "__main__":
    main()
