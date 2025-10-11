#!/usr/bin/env python3
"""
garmin_to_notion.py
Robust Garmin -> Notion sync script:
- Inspects Notion DB schema and maps your intended fields to actual property names
- Handles title vs rich_text differences
- Converts timestamps (ms/s) to ISO string
- Debug/test push mode to verify integration & permissions
"""

import os
import sys
import datetime
import time
import logging
from typing import Any, Dict, Optional

# pip: garminconnect, notion-client
from garminconnect import Garmin
from notion_client import Client
from notion_client.helpers import get_id  # may be helpful in some client versions

# ---------------------------
# CONFIG / ENV
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# Minimal validation
required = {
    "GARMIN_USERNAME": GARMIN_USERNAME,
    "GARMIN_PASSWORD": GARMIN_PASSWORD,
    "NOTION_TOKEN": NOTION_TOKEN,
    "NOTION_HEALTH_DB_ID": NOTION_HEALTH_DB_ID,
    "NOTION_ACTIVITIES_DB_ID": NOTION_ACTIVITIES_DB_ID,
}
missing = [k for k, v in required.items() if not v]
if missing:
    logging.error("Missing required environment variables: %s", ", ".join(missing))
    sys.exit(2)

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
# HELPERS: Garmin timestamp -> ISO
# ---------------------------
def to_iso(ts: Optional[Any]) -> Optional[str]:
    """Convert Garmin timestamps to ISO strings.
    Accepts: ISO string, float/int (seconds or milliseconds), or None.
    """
    if not ts:
        return None
    if isinstance(ts, str):
        # assume it's already iso-like; try to normalize
        try:
            # If string contains timezone or 'T', assume ISO
            if "T" in ts:
                return ts
            # fallback: try parse YYYY-MM-DD
            datetime.datetime.fromisoformat(ts)
            return ts
        except Exception:
            return ts
    try:
        t = float(ts)
    except Exception:
        return None
    # If it's very large, it's likely milliseconds
    if t > 1e12:
        t = t / 1000.0
    # if it's clearly seconds timestamp
    try:
        return datetime.datetime.utcfromtimestamp(t).isoformat() + "Z"
    except Exception:
        return None

# ---------------------------
# NOTION: schema inspection + mapping helpers
# ---------------------------
def fetch_db_schema(db_id: str) -> Dict[str, Any]:
    logging.debug("Retrieving Notion DB schema for %s", db_id)
    return notion.databases.retrieve(db_id)

def normalize_name(name: str) -> str:
    return "".join(name.lower().split())

def find_property_key(db_props: Dict[str, Any], desired_name: str) -> Optional[str]:
    """Find the actual property name/key in db_props for a desired friendly name.
    Matching: case-insensitive and whitespace-insensitive.
    Returns the property key as used by Notion (exact).
    """
    if not desired_name:
        return None
    norm = normalize_name(desired_name)
    for prop_key in db_props.keys():
        if normalize_name(prop_key) == norm:
            return prop_key
    # fallback: try partial matches (starts/contains)
    for prop_key in db_props.keys():
        if norm in normalize_name(prop_key) or normalize_name(prop_key) in norm:
            return prop_key
    return None

def build_property_for_type(prop_type: str, value: Any) -> Any:
    """Return the property value formatted for Notion based on the property type."""
    if value is None:
        # Notion expects the key to exist; we'll pass None-like values per type
        if prop_type == "number":
            return {"number": None}
        if prop_type == "title":
            return {"title": []}
        if prop_type == "rich_text":
            return {"rich_text": []}
        if prop_type == "date":
            return {"date": None}
        if prop_type == "checkbox":
            return {"checkbox": False}
        if prop_type in ("select", "multi_select"):
            return {prop_type: None if prop_type == "select" else []}
        if prop_type == "people":
            return {"people": []}
        if prop_type == "url":
            return {"url": None}
        return {prop_type: None}
    # handle actual values
    if prop_type == "number":
        try:
            return {"number": float(value)}
        except Exception:
            logging.warning("Could not convert %r to number", value)
            return {"number": None}
    if prop_type == "title":
        return {"title": [{"text": {"content": str(value)}}]}
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}
    if prop_type == "date":
        iso = to_iso(value)
        return {"date": {"start": iso}} if iso else {"date": None}
    if prop_type == "checkbox":
        return {"checkbox": bool(value)}
    if prop_type == "select":
        return {"select": {"name": str(value)}}
    if prop_type == "multi_select":
        items = value if isinstance(value, (list, tuple)) else [value]
        return {"multi_select": [{"name": str(x)} for x in items]}
    if prop_type == "people":
        # expects array of objects with id; we can't guess ids here so leave empty
        return {"people": []}
    if prop_type == "url":
        return {"url": str(value)}
    # fallback: try rich_text
    return {"rich_text": [{"text": {"content": str(value)}}]}

def build_notition_payload_from_mapping(db_id: str, desired_map: Dict[str, Any]) -> Dict[str, Any]:
    """
    desired_map: mapping of friendly property name -> value
    Looks up the actual property keys and types in Notion DB and builds the proper payload.
    """
    db = fetch_db_schema(db_id)
    db_props = db.get("properties", {})
    payload = {}
    for friendly_name, value in desired_map.items():
        prop_key = find_property_key(db_props, friendly_name)
        if not prop_key:
            logging.warning("Property '%s' not found in DB %s - skipping", friendly_name, db_id)
            continue
        prop_meta = db_props[prop_key]
        prop_type = prop_meta.get("type")
        formatted = build_property_for_type(prop_type, value)
        payload[prop_key] = formatted
        logging.debug("Mapped '%s' -> '%s' (type=%s) : %s", friendly_name, prop_key, prop_type, formatted)
    return payload

# ---------------------------
# SAFE GARMIN FETCH
# ---------------------------
def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logging.warning("Garmin function %s failed: %s", getattr(func, "__name__", func), e)
        return None

# ---------------------------
# MAIN SYNC FLOW
# ---------------------------
def main():
    # login to garmin
    logging.info("Logging into Garmin...")
    try:
        garmin.login()
    except Exception as e:
        logging.error("Garmin login failed: %s", e)
        sys.exit(3)

    # date selection
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    yesterday_str = yesterday.isoformat()
    logging.info("Collecting Garmin data for %s", yesterday_str)

    # fetch from garmin
    activities = safe_fetch(garmin.get_activities, 0, 10) or []
    steps = safe_fetch(garmin.get_daily_steps, yesterday_str, yesterday_str) or []
    sleep_data = safe_fetch(garmin.get_sleep_data, yesterday_str) or {}
    body_battery = safe_fetch(garmin.get_body_battery, yesterday_str, yesterday_str) or []
    body_comp = safe_fetch(garmin.get_body_composition, yesterday_str) or {}
    readiness = safe_fetch(garmin.get_training_readiness, yesterday_str) or {}
    status = safe_fetch(garmin.get_training_status, yesterday_str) or {}
    stats = safe_fetch(garmin.get_stats_and_body, yesterday_str) or {}

    # small helpers for safe extraction:
    def se(data, key):
        if not data:
            return None
        if isinstance(data, dict):
            return data.get(key)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0].get(key)
        return None

    # Parse
    sleep_daily = (sleep_data.get("dailySleepDTO") if isinstance(sleep_data, dict) else {}) or {}
    sleep_score = se(sleep_daily, "sleepScore")
    bed_time = se(sleep_daily, "sleepStartTimestampGMT") or se(sleep_daily, "sleepStartTimeInSeconds")
    wake_time = se(sleep_daily, "sleepEndTimestampGMT") or se(sleep_daily, "sleepEndTimeInSeconds")
    body_battery_value = se(body_battery, "bodyBatteryValue")
    body_weight = None
    if isinstance(body_comp, dict) and body_comp.get("dateWeightList"):
        try:
            body_weight = body_comp["dateWeightList"][0].get("weight")
        except Exception:
            body_weight = None
    training_readiness = se(readiness, "score")
    training_status_val = se(status, "trainingStatus")
    resting_hr = se(stats, "restingHeartRate")
    stress = se(stats, "stressLevelAvg")
    calories = se(stats, "totalKilocalories")

    # Steps aggregation
    steps_total = 0
    if isinstance(steps, list):
        try:
            steps_total = sum((int(item.get("totalSteps", 0)) for item in steps))
        except Exception:
            steps_total = 0

    # Build desired friendly map
    health_map = {
        "Date": yesterday_str,
        "Steps": steps_total if steps_total > 0 else None,
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

    # Debug: print raw garmin objects
    logging.debug("Raw Garmin objects (summaries):")
    logging.debug("activities count: %d", len(activities) if activities else 0)
    logging.debug("steps: %r", steps[:2] if isinstance(steps, list) else steps)
    logging.debug("sleep_daily: %r", {k: sleep_daily.get(k) for k in ("sleepScore", "sleepStartTimestampGMT", "sleepEndTimestampGMT")} if sleep_daily else {})
    logging.debug("body_comp keys: %r", list(body_comp.keys()) if isinstance(body_comp, dict) else body_comp)
    logging.debug("body_battery: %r", body_battery)
    logging.debug("readiness/status/stats: %r / %r / %r", readiness, status, stats)

    # TEST PUSH PHASE: try to create a test row to confirm permissions and mapping
    logging.info("Performing a minimal test push to Notion to verify integration & permissions...")
    test_map = {
        "Date": yesterday_str,
        "Steps": 42,
        "Body Weight": 70,
        "Training Status": "Test OK",
    }

    try:
        test_payload = build_notition_payload_from_mapping(NOTION_HEALTH_DB_ID, test_map)
        logging.info("DEBUG: Test payload to push: %s", test_payload)
        resp = notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=test_payload)
        logging.info("Test page created: id=%s", resp.get("id"))
        logging.debug("Test page full response: %s", resp)
        # Optionally delete the test row (commented out)
        # notion.blocks.delete(resp["id"])
    except Exception as e:
        logging.error("Test push failed — this usually indicates integration permissions/database id mismatch or property type mismatch. Error: %s", e)
        logging.error("Make sure your integration is added to the database and has insert access, and that DB ID and NOTION_TOKEN are correct.")
        garmin.logout()
        sys.exit(4)

    # Build real payload for health
    health_payload = build_notition_payload_from_mapping(NOTION_HEALTH_DB_ID, health_map)
    logging.info("Final health payload to push: %s", health_payload)
    try:
        resp = notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_payload)
        logging.info("✅ Health Metrics added for %s - page id: %s", yesterday_str, resp.get("id"))
        logging.debug("Notion response: %s", resp)
    except Exception as e:
        logging.error("Failed to push health metrics: %s", e)
        logging.error("Payload was: %s", health_payload)

    # Activities
    logging.info("Preparing activity rows (%d) ...", len(activities) if activities else 0)
    for act in activities:
        # safe extraction
        act_date = (act.get("startTimeLocal") or "")[:10]
        dist_km = act.get("distance") / 1000 if act.get("distance") else None
        dur_min = round(act.get("duration") / 60, 1) if act.get("duration") else None
        act_map = {
            "Date": act_date or yesterday_str,
            "Activity Name": act.get("activityName") or act.get("activityType", {}).get("typeKey"),
            "Distance (km)": dist_km,
            "Calories": act.get("calories"),
            "Duration (min)": dur_min,
            "Type": act.get("activityType", {}).get("typeKey") if act.get("activityType") else None,
        }
        props = build_notition_payload_from_mapping(NOTION_ACTIVITIES_DB_ID, act_map)
        logging.debug("Activity payload: %s", props)
        try:
            aresp = notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=props)
            logging.info("Logged activity: %s -> page id %s", act_map.get("Activity Name"), aresp.get("id"))
        except Exception as e:
            logging.error("Failed to log activity %s: %s", act_map.get("Activity Name"), e)

    garmin.logout()
    logging.info("🏁 Sync complete.")

if __name__ == "__main__":
    main()
