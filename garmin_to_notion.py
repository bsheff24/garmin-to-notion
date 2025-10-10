import os
import datetime
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")

# ---------------------------
# CLIENTS
# ---------------------------
notion = Client(auth=NOTION_TOKEN)
garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin.login()

# ---------------------------
# HELPERS
# ---------------------------
def notion_date(dt):
    if not dt:
        return {"date": None}
    if isinstance(dt, str):
        return {"date": {"start": dt}}
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    return {"number": float(value)} if value is not None else {"number": None}

def notion_text(value):
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": str(value)}}]}

def notion_select(value):
    if not value:
        return {"select": None}
    return {"select": {"name": str(value)}}

def already_logged(db_id, date_str):
    response = notion.databases.query(
        **{
            "database_id": db_id,
            "filter": {"property": "Date", "date": {"equals": date_str}},
        }
    )
    return len(response.get("results", [])) > 0

# ---------------------------
# DATE SETUP
# ---------------------------
today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.isoformat()

print(f"📅 Collecting Garmin data for {yesterday_str}")

# ---------------------------
# FETCH DATA FROM GARMIN
# ---------------------------
def safe_fetch(func, *args):
    try:
        return func(*args)
    except Exception as e:
        print(f"⚠️ {func.__name__} unavailable:", e)
        return None

activities = safe_fetch(garmin.get_activities, 0, 10)
steps = safe_fetch(garmin.get_daily_steps, yesterday_str, yesterday_str)
sleep_data = safe_fetch(garmin.get_sleep_data, yesterday_str)
body_battery = safe_fetch(garmin.get_body_battery, yesterday_str, yesterday_str)
body_comp = safe_fetch(garmin.get_body_composition, yesterday_str)
readiness = safe_fetch(garmin.get_training_readiness, yesterday_str)
status = safe_fetch(garmin.get_training_status, yesterday_str)
stats = safe_fetch(garmin.get_stats_and_body, yesterday_str)

# ---------------------------
# PARSE HEALTH METRICS
# ---------------------------
def safe_extract(data, key):
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0].get(key)
    return None

sleep_score = safe_extract(sleep_data, "sleepScore")
bed_time = safe_extract(sleep_data, "sleepStartTimestampGMT")
wake_time = safe_extract(sleep_data, "sleepEndTimestampGMT")

body_battery_value = safe_extract(body_battery, "bodyBatteryValue")
body_weight = safe_extract(body_comp, "weight")
training_readiness = safe_extract(readiness, "trainingReadinessScore")
training_status = safe_extract(status, "trainingStatus")
resting_hr = safe_extract(stats, "restingHeartRate")
stress = safe_extract(stats, "stressLevelAvg")
calories = safe_extract(stats, "totalKilocalories")

steps_total = 0
if isinstance(steps, list) and len(steps) > 0:
    steps_total = sum(item.get("totalSteps", 0) for item in steps)

# ---------------------------
# PUSH TO NOTION - HEALTH METRICS
# ---------------------------
if not already_logged(NOTION_HEALTH_DB_ID, yesterday_str):
    try:
        properties = {
            "Date": notion_date(yesterday_str),
            "Steps": notion_number(steps_total if steps_total > 0 else None),
            "Body Weight": notion_number(body_weight),
            "Body Battery": notion_number(body_battery_value),
            "Sleep Score": notion_number(sleep_score),
            "Bedtime": notion_date(bed_time),
            "Wake Time": notion_date(wake_time),
            "Training Readiness": notion_number(training_readiness),
            "Training Status": notion_select(training_status),
            "Resting HR": notion_number(resting_hr),
            "Stress": notion_number(stress),
            "Calories Burned": notion_number(calories),
        }

        notion.pages.create(
            parent={"database_id": NOTION_HEALTH_DB_ID},
            properties=properties,
        )
        print(f"✅ Added Health Metrics for {yesterday_str}")

    except Exception as e:
        print("⚠️ Failed to push health metrics:", e)
else:
    print("ℹ️ Health Metrics already logged for", yesterday_str)

# ---------------------------
# PUSH TO NOTION - ACTIVITIES
# ---------------------------
if activities:
    for act in activities:
        try:
            act_date = act.get("startTimeLocal", "")[:10]
            if already_logged(NOTION_ACTIVITIES_DB_ID, act_date):
                continue

            props = {
                "Date": notion_date(act_date),
                "Activity Name": notion_text(act.get("activityName")),
                "Distance (km)": notion_number(act.get("distance") / 1000 if act.get("distance") else None),
                "Calories": notion_number(act.get("calories")),
                "Duration (min)": notion_number(round(act.get("duration") / 60, 1) if act.get("duration") else None),
                "Type": notion_text(act.get("activityType", {}).get("typeKey")),
            }

            notion.pages.create(
                parent={"database_id": NOTION_ACTIVITIES_DB_ID},
                properties=props,
            )
            print(f"✅ Logged activity: {act.get('activityName')}")

        except Exception as e:
            print(f"⚠️ Failed to log activity: {e}")

garmin.logout()
print("🏁 Sync complete.")
