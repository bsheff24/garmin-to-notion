import os
from datetime import datetime, timedelta
from notion_client import Client as NotionClient
from garth import Client as GarminClient

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
today = datetime.now()
yesterday = today - timedelta(days=1)
yesterday_str = yesterday.strftime("%Y-%m-%d")

print(f"📅 Collecting Garmin data for {yesterday_str}")

# === Collect Garmin Data ===
daily_summary = garmin.get_daily_summary(yesterday_str)
body_battery = garmin.get_body_battery(yesterday_str)
weight = garmin.get_body_composition(yesterday_str)
sleep_data = garmin.get_sleep_data(yesterday_str)
readiness = garmin.get_training_readiness(yesterday_str)
status = garmin.get_training_status(yesterday_str)

steps = daily_summary.get("steps", 0)
calories = daily_summary.get("totalKilocalories", 0)
resting_hr = daily_summary.get("restingHeartRate", 0)
stress = daily_summary.get("stress", 0)
body_weight = weight.get("weight", 0)

bb_min = body_battery.get("bodyBatteryMin", 0)
bb_max = body_battery.get("bodyBatteryMax", 0)

sleep_score = sleep_data.get("sleepScoreFeedbackDTO", {}).get("overallScore", 0)
bedtime = sleep_data.get("sleepStartTimestampGMT")
waketime = sleep_data.get("sleepEndTimestampGMT")

training_readiness = readiness.get("trainingReadinessScore", 0)

# === FIXED: Training Status Mapping ===
raw_status = (status.get("trainingStatus", {}) or {}).get("primaryStatus", "").lower()
status_map = {
    "peaking": "Peaking",
    "recovery": "Recovery",
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
bed_dt = datetime.fromtimestamp(bedtime / 1000).astimezone() if bedtime else None
wake_dt = datetime.fromtimestamp(waketime / 1000).astimezone() if waketime else None

# === Display Parsed Data ===
print("🔍 Parsed Garmin metrics:")
print(f"Steps: {steps}, Body Weight: {body_weight}")
print(f"Body Battery Min: {bb_min}, Max: {bb_max}")
print(f"Sleep Score: {sleep_score}, Bedtime: {bed_dt}, Wake Time: {wake_dt}")
print(f"Training Readiness: {training_readiness}, Training Status: {training_status}")
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
    "Training Readiness": {"number": training_readiness},
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
