import os
import pprint
from datetime import datetime, timedelta
from notion_client import Client as NotionClient
from garth import Client as GarminClient

# === Environment variables ===
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")

# === Initialize Garmin client (Updated for new garth versions) ===
garmin = GarminClient()
garmin.login(GARMIN_USERNAME, GARMIN_PASSWORD)

# === Initialize Notion client ===
notion = NotionClient(auth=NOTION_TOKEN)

# === Date handling ===
today = datetime.now()
yesterday = today - timedelta(days=1)
yesterday_str = yesterday.strftime("%Y-%m-%d")

print(f"📅 Collecting Garmin data for {yesterday_str}")

try:
    # ✅ Updated Garmin API calls
    daily_summary = garmin.get_daily_summary(yesterday_str)
    body_battery = garmin.get_body_battery(yesterday_str)
    weight = garmin.get_body_composition(yesterday_str)
    sleep_daily = garmin.get_sleep_data(yesterday_str)
    readiness = garmin.get_training_readiness(yesterday_str)
    status = garmin.get_training_status(yesterday_str)

    stats = daily_summary

    # === Extract Garmin data ===
    steps = stats.get("steps", 0)
    calories = stats.get("totalKilocalories", 0)
    resting_hr = stats.get("restingHeartRate", 0)
    stress = stats.get("stress", 0)
    body_weight = weight.get("weight", 0)

    bb_min = body_battery.get("bodyBatteryMin", 0)
    bb_max = body_battery.get("bodyBatteryMax", 0)

    sleep_score = sleep_daily.get("sleepScoreFeedbackDTO", {}).get("overallScore", 0)
    bedtime = sleep_daily.get("sleepStartTimestampGMT")
    waketime = sleep_daily.get("sleepEndTimestampGMT")

    training_readiness = readiness.get("trainingReadinessScore", 0)

    # === Normalize Training Status ===
    raw_status = (status.get("trainingStatus", {}) or {}).get("primaryStatus", "").lower()

    status_map = {
        "peaking": "Peaking",
        "recovery": "Recovery",
        "maintaining": "Maintaining",
        "productive": "Productive",
        "unproductive": "Unproductive",
        "detraining": "Detraining",
        "strained": "Strained",
    }
    training_status = status_map.get(raw_status, "Maintaining")

    # === Debug Print ===
    print("🔍 Parsed Garmin metrics:")
    print(f"Steps: {steps}, Body Weight: {body_weight}")
    print(f"Body Battery Min: {bb_min}, Max: {bb_max}")
    print(f"Sleep Score: {sleep_score}, Bedtime: {bedtime}, Wake Time: {waketime}")
    print(f"Training Readiness: {training_readiness}, Training Status: {training_status}")
    print(f"Resting HR: {resting_hr}, Stress: {stress}, Calories: {calories}")

    # === Format Timestamps ===
    bed_dt = datetime.fromtimestamp(bedtime / 1000).astimezone() if bedtime else None
    wake_dt = datetime.fromtimestamp(waketime / 1000).astimezone() if waketime else None
    formatted_title = yesterday.strftime("%m/%d/%Y")

    # === Build Notion Payload ===
    notion_page = {
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
        notion_page["Bedtime"] = {"date": {"start": bed_dt.isoformat()}}
    if wake_dt:
        notion_page["Wake Time"] = {"date": {"start": wake_dt.isoformat()}}

    pprint.pprint(notion_page)

    # === Push to Notion ===
    print("📤 Pushing Garmin health metrics to Notion...")
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=notion_page)
    print(f"✅ Synced health metrics for {formatted_title}")

except Exception as e:
    print("❌ An error occurred while syncing Garmin data:")
    import traceback

    traceback.print_exc()

