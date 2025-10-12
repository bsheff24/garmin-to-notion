import os
import datetime
import pprint
from garth import Client
from notion_client import Client as NotionClient

# --- Helper functions -------------------------------------------------------

def extract_value(obj, keys):
    """Safely walk nested dicts."""
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return None
    return obj

def notion_date(dt):
    """Format date for Notion."""
    if not dt:
        return {"date": None}
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
            except Exception:
                return {"date": None}
    return {"date": {"start": dt.isoformat()}}

# --- Auth ------------------------------------------------------------------

GARMIN_USERNAME = os.environ["GARMIN_USERNAME"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_HEALTH_DB_ID = os.environ["NOTION_HEALTH_DB_ID"]

client = Client()
client.login(GARMIN_USERNAME, GARMIN_PASSWORD)

notion = NotionClient(auth=NOTION_TOKEN)

# --- Date handling ----------------------------------------------------------

today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.isoformat()

print(f"📅 Collecting Garmin data for {yesterday_str}")

# --- Pull data from Garmin --------------------------------------------------

wellness = client.get_wellness(yesterday_str)
body_battery = client.get_body_battery(yesterday_str)
weight = client.get_body_composition(yesterday_str)
sleep_daily = client.get_sleep_data(yesterday_str)
readiness = client.get_training_readiness(yesterday_str)
status = client.get_training_status(yesterday_str)
stats = client.get_stats(yesterday_str)

# --- Debug raw payloads -----------------------------------------------------

print("=== sleep_daily ===")
pprint.pprint(sleep_daily)
print("=== body_battery ===")
pprint.pprint(body_battery)
print("=== readiness ===")
pprint.pprint(readiness)
print("=== status ===")
pprint.pprint(status)
print("=== stats ===")
pprint.pprint(stats)

# --- Parse metrics ----------------------------------------------------------

steps = extract_value(stats, ["totalSteps"]) or 0
calories = extract_value(stats, ["totalKilocalories"]) or 0
resting_hr = extract_value(stats, ["restingHeartRate"]) or 0

body_weight = 0
if weight and isinstance(weight, list) and len(weight) > 0:
    body_weight = extract_value(weight[0], ["weight"]) or 0

# Body Battery min/max
bb_min = bb_max = 0
if body_battery and isinstance(body_battery, list) and len(body_battery) > 0:
    bb_min = extract_value(body_battery[0], ["minBatteryLevel"]) or 0
    bb_max = extract_value(body_battery[0], ["maxBatteryLevel"]) or 0

# Sleep score
sleep_score = 0
if sleep_daily:
    sleep_score = (
        extract_value(sleep_daily, ["sleepScores", "overall", "value"])
        or extract_value(sleep_daily, ["sleepScore"])
        or extract_value(sleep_daily, ["overallScore"])
        or 0
    )

# Bedtime / wake time
try:
    bed_time_ms = extract_value(sleep_daily, ["sleepStartTimestampGMT"])
    wake_time_ms = extract_value(sleep_daily, ["sleepEndTimestampGMT"])
    if bed_time_ms and wake_time_ms:
        bed_time = datetime.datetime.fromtimestamp(bed_time_ms / 1000, tz=datetime.timezone.utc).astimezone()
        wake_time = datetime.datetime.fromtimestamp(wake_time_ms / 1000, tz=datetime.timezone.utc).astimezone()
    else:
        bed_time = wake_time = None
except Exception:
    bed_time = wake_time = None

# Training readiness
training_readiness = 0
if readiness and isinstance(readiness, list) and len(readiness) > 0:
    training_readiness = extract_value(readiness[0], ["readinessScore"]) or 0

# --- Training status mapping & override ------------------------------------

status_map = {
    0: "No Status",
    1: "Detraining",
    2: "Maintaining",
    3: "Recovery",
    4: "Productive",
    5: "Peaking",
    6: "Strained",
    7: "Unproductive"
}

current_status_val = extract_value(status, ["currentStatus", "trainingStatus"])
training_status_val = (
    status_map.get(int(current_status_val), "Maintaining")
    if isinstance(current_status_val, (int, float))
    else "Maintaining"
)

# Search through all status / readiness / body_battery fields for keywords
feedback_fields = []
for block in [status, readiness, body_battery]:
    if isinstance(block, dict):
        for k, v in block.items():
            if isinstance(v, str):
                feedback_fields.append(v.upper())
            elif isinstance(v, (dict, list)):
                feedback_fields.append(str(v).upper())

for text in feedback_fields:
    if "RECOV" in text or "REST" in text:
        training_status_val = "Recovery"
        break
    if "UNPRODUCTIVE" in text:
        training_status_val = "Unproductive"
        break
    if "DETRAIN" in text:
        training_status_val = "Detraining"
        break
    if "STRAIN" in text:
        training_status_val = "Strained"
        break

# --- Stress (optional, still pulled) ---------------------------------------

stress_val = extract_value(stats, ["stressLevel"]) or extract_value(stats, ["avgStressLevel"]) or 0

# --- Push to Notion --------------------------------------------------------

name_val = yesterday.strftime("%m/%d/%Y")

health_props = {
    "Name": {"title": [{"text": {"content": name_val}}]},
    "Date": notion_date(datetime.datetime.now()),
    "Steps": {"number": float(steps)},
    "Body Weight": {"number": float(body_weight)},
    "Body Battery (Min)": {"number": float(bb_min)},
    "Body Battery (Max)": {"number": float(bb_max)},
    "Sleep Score": {"number": float(sleep_score)},
    "Bedtime": notion_date(bed_time),
    "Wake Time": notion_date(wake_time),
    "Training Readiness": {"number": float(training_readiness)},
    "Resting HR": {"number": float(resting_hr)},
    "Stress": {"number": float(stress_val)},
    "Calories Burned": {"number": float(calories)},
    "Training Status": {"select": {"name": training_status_val}},
}

print("📤 Pushing Garmin health metrics to Notion...")
pprint.pprint(health_props)

notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=health_props)
print(f"✅ Synced health metrics for {yesterday_str}")

