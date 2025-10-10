import os
from datetime import datetime
from notion_client import Client
from garminconnect import Garmin

# ----------------------
# 1. Load environment variables
# ----------------------
GARMIN_USERNAME = os.environ.get("GARMIN_USERNAME")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.environ.get("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.environ.get("NOTION_ACTIVITIES_DB_ID")
NOTION_STEPS_DB_ID = os.environ.get("NOTION_STEPS_DB_ID")
NOTION_SLEEP_DB_ID = os.environ.get("NOTION_SLEEP_DB_ID")
NOTION_PR_DB_ID = os.environ.get("NOTION_PR_DB_ID")

# ----------------------
# 2. Connect to Notion
# ----------------------
notion = Client(auth=NOTION_TOKEN)

# ----------------------
# 3. Connect to Garmin
# ----------------------
garmin_client = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin_client.login()  # Works in GitHub workflow

# ----------------------
# 4. Helpers
# ----------------------
def km_to_miles(km):
    return round(km * 0.621371, 2)

def min_per_km_to_min_per_mile(pace_km):
    return round(pace_km / 0.621371, 2)

def already_logged(db_id, date_str):
    results = notion.databases.query(
        database_id=db_id,
        filter={"property": "Date", "date": {"equals": date_str}}
    )
    return len(results.get("results", [])) > 0

# ----------------------
# 5. Prepare today's date
# ----------------------
today_str = datetime.now().strftime("%Y-%m-%d")

# ----------------------
# 6. Pull Garmin data
# ----------------------

# Latest activity
activities = garmin_client.get_activities(1)
activity_row = {}
if activities:
    act = activities[0]
    activity_row = {
        "Date": {"date": {"start": act.get("startTimeLocal")[:10]}},
        "Distance (mi)": {"number": km_to_miles(act.get("distance", 0)/1000)},
        "Duration (min)": {"number": round(act.get("duration",0)/60,1)},
        "Avg Pace (min/mi)": {"number": min_per_km_to_min_per_mile(act.get("averageSpeed",0) and 60/act["averageSpeed"] or 0)}
    }

# Steps
steps_data = garmin_client.get_daily_steps(today_str, today_str)
steps_row = {
    "Date": {"date": {"start": today_str}},
    "Steps": {"number": steps_data.get("steps", 0)}
}

# Sleep
sleep_data = garmin_client.get_sleep_data(today_str)
sleep_row = {
    "Date": {"date": {"start": today_str}},
    "Sleep Score": {"number": sleep_data.get("sleepScore", 0)},
    "Bedtime": {"date": {"start": sleep_data.get("startTime")}},
    "Wake Time": {"date": {"start": sleep_data.get("endTime")}}
}

# Body battery
body_battery_data = garmin_client.get_body_battery(today_str, today_str)
body_row = {
    "Date": {"date": {"start": today_str}},
    "Body Battery": {"number": body_battery_data.get("bodyBattery", 0)}
}

# Training readiness & status
training_readiness_data = garmin_client.get_training_readiness(today_str, today_str)
training_row = {
    "Date": {"date": {"start": today_str}},
    "Training Readiness": {"number": training_readiness_data.get("score", 0)},
    "Training Status": {"rich_text": [{"text": {"content": training_readiness_data.get("status", "")}}]}
}

# Personal records
pr_list = garmin_client.get_personal_record()
pr_row = {}
for pr in pr_list:
    pr_row[pr["type"]] = {"number": pr["value"]}

# ----------------------
# 7. Push to Notion
# ----------------------
# Activities DB
if activity_row and not already_logged(NOTION_ACTIVITIES_DB_ID, activity_row["Date"]["date"]["start"]):
    notion.pages.create(parent={"database_id": NOTION_ACTIVITIES_DB_ID}, properties=activity_row)
    print(f"✅ Activity added for {activity_row['Date']['date']['start']}")
else:
    print(f"⚠️ Activity already logged or missing")

# Steps DB
if steps_row and not already_logged(NOTION_STEPS_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_STEPS_DB_ID}, properties=steps_row)
    print(f"✅ Steps added for {today_str}")
else:
    print(f"⚠️ Steps already logged or missing")

# Sleep DB
if sleep_row and not already_logged(NOTION_SLEEP_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_SLEEP_DB_ID}, properties=sleep_row)
    print(f"✅ Sleep added for {today_str}")
else:
    print(f"⚠️ Sleep already logged or missing")

# Body Battery / Health Metrics DB
if body_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=body_row)
    print(f"✅ Body battery added for {today_str}")
else:
    print(f"⚠️ Body battery already logged or missing")

# Training Readiness DB
if training_row and not already_logged(NOTION_HEALTH_DB_ID, today_str):
    notion.pages.create(parent={"database_id": NOTION_HEALTH_DB_ID}, properties=training_row)
    print(f"✅ Training readiness added for {today_str}")
else:
    print(f"⚠️ Training readiness already logged or missing")

# Personal Records DB
if pr_row:
    pr_row["Date"] = {"date": {"start": today_str}}
    if not already_logged(NOTION_PR_DB_ID, today_str):
        notion.pages.create(parent={"database_id": NOTION_PR_DB_ID}, properties=pr_row)
        print(f"✅ Personal records updated for {today_str}")
    else:
        print(f"⚠️ Personal records already logged for {today_str}")

