import os
from datetime import datetime, timedelta, timezone
from notion_client import Client as NotionClient
from garth import Client as GarminClient
import pprint

# === Environment Variables ===
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")

# === Initialize Clients ===
garmin = GarminClient()
garmin.login(GARMIN_USERNAME, GARMIN_PASSWORD)

notion = NotionClient(auth=NOTION_TOKEN)

# === Dates ===
today = datetime.now(timezone.utc)
yesterday = today - timedelta(days=1)
yesterday_str = yesterday.strftime("%Y-%m-%d")

print(f"📅 Collecting Garmin data for {yesterday_str}")

# === Get Garmin Data (modern endpoints) ===
try:
    wellness = garmin.connectapi(f"/wellness-service/wellness/daily/{yesterday_str}")
    body_battery = garmin.connectapi(f"/wellness-service/wellness/dailyBodyBattery/{yesterday_str}")
    sleep_data = garmin.connectapi(f"/sleep-service/sleep/daily/{yesterday_str}")
    training_readiness = garmin.connectapi(f"/training-service/trainingReadiness/daily/{yesterday_str}")
    training_status_data = garmin.connectapi("/training-service/training/status")
    weight_data = garmin.connectapi("/weight-service/weight/date/" + yesterday_str)
except Exception as e:
    print("❌ Error fetching Garmin data:")
    print(e)
    raise SystemExit(1)

# === Parse Garmin Data ===
steps = wellness.get("steps", 0)
calories = wellness.get("totalKilocalories", 0)
resting_hr = wellness.get("restingHeartRate", 0)
stress = wellness.get("stressLevel", 0)

body_weight = (
    weight_data[0]["weight"] if isinstance(weight_data, list) and len(weight_data) > 0 else 0
)

bb_min = body_battery.get("bodyBatteryMin", 0)
bb_max = body_battery.get("bodyBatteryMax", 0)

sleep_score = sleep_data.get("sleepScoreFeedbackDTO", {}).get("overallScore", 0)
bedtime = sleep_data.get("sleepStartTimestampGMT")
waketime = sleep_data.get("sleepEndTimestampGMT")

training_readiness_score = training_readiness.get("trainingReadinessScore", 0)

# === Fix: Map Garmin Training Status ===
raw_status = (
    training_status_data.get("primaryTrainingStatus", "")
    or training_status_data.get("primaryStatus", "")
).lower()

status_map = {
    "recovery": "Recovery",
    "peaking": "Peaking",
    "maintaining": "Maintaining",
    "productive": "Productive",
    "unproductive": "Unproductive",
    "detraining": "Detraining",
    "strained": "Strained",
    "off": "Off",
    "transition": "Transition",
}

training_status = status_map.get(raw_status, "Maintaining")

# === Format Sleep Times ===
bed_dt = datetime.fromtimestamp(bedtime / 1000, tz=timezone.utc) if bedtime else None
wake_dt = datetime.fromtimestamp(waketime / 1000, tz=timezone.utc) if waketime else None

# === Display Parsed Data ===
print("🔍 Parsed Garmin metrics:")
print(f"Steps: {steps}, Body Weight: {body_weight}")
print(f"Body Battery Min: {bb_min}, Max: {bb_max}")
print(f"Sleep Score: {sleep_score}, Bedtime: {bed_dt}, Wake Time: {wake_dt}")
print(f"Training Readiness: {training_readiness_score}, Training Status: {training_status}")
print(f"Resting HR: {resting_hr}, Stress: {stress}, Calories: {calories}")

# === Push to Notion ===
formatted_title = yesterday.strftime("%m/%d/%Y")

page_data = {
    "Name": {"title": [{"text": {"content": formatted_title}}]},
    "Date": {"date": {"start": datetime.now().isoformat()}},
    "Steps": {"number": steps},
    "Calories Burned": {"number": calories},
    "Resting HR": {"number": resting_hr},
    "Stress": {"number": stress},
    "Body Weight": {"number": body_weight},
    "Body Battery (Min)": {"number": bb_min},
    "Body Battery (Max)": {"number": bb_max},
    "Sleep Score": {"number": sleep_score},
    "Training Readiness": {"number": training_readiness_score},
    "Training Status": {"select": {"name": training_status}},
}

if bed_dt:
    page_data["Bedtime"] = {"date": {"start": bed_dt.isoformat()}}
if wake_dt:
    page_data["Wake Time"] = {"date": {"start": wake_dt.isoformat()}}

print("📤 Pushing Garmin health metrics to Notion...")

try:
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=page_data)
    print("✅ Synced health metrics for", yesterday_str)
except Exception as e:
    print("❌ Failed to push data to Notion")
    import traceback
    traceback.print_exc()
