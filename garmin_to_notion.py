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
    if prop_type == "numb_

