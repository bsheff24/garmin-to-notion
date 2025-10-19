import os
import datetime
import logging
import pprint
from garminconnect import Garmin
from notion_client import Client

# ---------------------------
# CONFIG
# ---------------------------
DEBUG = True
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_HEALTH_DB_ID = os.getenv("NOTION_HEALTH_DB_ID")
NOTION_ACTIVITIES_DB_ID = os.getenv("NOTION_ACTIVITIES_DB_ID")

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------
# HELPERS
# ---------------------------
def notion_date(dt):
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    dt = datetime.datetime.strptime(dt, "%Y-%m-%d")
                except:
                    return None
    if isinstance(dt, datetime.datetime):
        dt = dt.astimezone(LOCAL_TZ)
    return {"date": {"start": dt.isoformat()}}

def notion_number(value):
    if value is None or value == 0:
        return None
    try:
        return {"number": float(value)}
    except:
        return None

def notion_select(value):
    if not value:
        return None
    return {"select": {"name": str(value)}}

def notion_title(value):
    return {"title": [{"text": {"content": str(value) if value else "N/A"}}]}

def notion_text(value):
    if value is None:
        return None
    return {"rich_text": [{"text": {"content": str(value)}}]}

def safe_fetch(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logging.warning(f"⚠️ {func.__name__} unavailable: {e}")
        return None

def extract_value(data, keys):
    if not data:
        return None
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                val = data[key]
                if isinstance(val, (int,float,str)):
                    return val
                nested = extract_value(val, keys)
                if nested is not None:
                    return nested
        for v in data.values():
            nested = extract_value(v, keys)
            if nested is not None:
                return nested
    elif isinstance(data, list):
        for item in data:
            nested = extract_value(item, keys)
            if nested is not None:
                return nested
    return None

def safe_stat(stats_list, keys):
    val = extract_value(stats_list, keys)
    if val == 0:
        return None
    return val

def format_activity_type(activity_type, activity_name=""):
    formatted_type = activity_type.replace('_', ' ').title() if activity_type else "Unknown"
    activity_subtype = formatted_type
    mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running"
    }
    if formatted_type in mapping:
        return mapping[formatted_type], formatted_type
    if "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if "barre" in activity_name.lower():
        return "Strength", "Barre"
    if "stretch" in activity_name.lower():
        return "Stretching", "Stretching"
    return formatted_type, formatted_type

def activity_exists(client, database_id, activity_date, activity_type, activity_name):
    query = client.databases.query(
        database_id=database_id,
        filter={
            "and":[
                {"property":"Date","date":{"equals":activity_date.split('T')[0]}},
                {"property":"Activity Name","title":{"equals":activity_name}},
                {"property":"Activity Type","select":{"equals":activity_type}}
            ]
        }
    )
    results = query.get('results',[])
    return results[0] if results else None

def needs_update(existing, new_props):
    existing_props = existing.get('properties',{})
    for key,value in new_props.items():
        if key not in existing_props:
            return True
        existing_val = existing_props[key]
        if "number" in value:
            if existing_val.get("number") != value["number"]:
                return True
        elif "select" in value:
            if (existing_val.get("select",{}).get("name") != value["select"]["name"]):
                return True
        elif "title" in value:
            if existing_val.get("title",[{}])[0].get("text",{}).get("content") != value["title"][0]["text"]["content"]:
                return True
        elif "date" in value:
            if existing_val.get("date",{}).get("start") != value["date"]["start"]:
                return True
        elif "rich_text" in value:
            if existing_val.get("rich_text",[{}])[0].get("text",{}).get("content") != value["rich_text"][0]["text"]["content"]:
                return True
    return False

# ---------------------------
# MAIN SCRIPT
# ---------------------------
def main():
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    iso_yesterday = yesterday.isoformat()

    notion = Client(auth=NOTION_TOKEN)
    garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
    garmin.login()

    # ---------------------------
    # HEALTH METRICS (yesterday only)
    # ---------------------------
    logging.info(f"📅 Collecting Garmin health data for {yesterday.strftime('%m/%d/%Y')}")
    steps = safe_fetch(garmin.get_daily_steps, iso_yesterday, iso_yesterday) or []
    sleep_data = safe_fetch(garmin.get_sleep_data, iso_yesterday) or {}
    body_battery = safe_fetch(garmin.get_body_battery, iso_yesterday, iso_yesterday) or []
    body_comp = safe_fetch(garmin.get_body_composition, iso_yesterday) or {}
    readiness = safe_fetch(garmin.get_training_readiness, iso_yesterday) or []
    status = safe_fetch(garmin.get_training_status, iso_yesterday) or []
    stats = safe_fetch(garmin.get_stats_and_body, iso_yesterday) or []

    sleep_daily = sleep_data.get("dailySleepDTO",{}) if sleep_data else {}
    sleep_score = extract_value(sleep_daily, ["sleepScores","overall","value"]) or None
    bed_time = sleep_daily.get("sleepStartTimestampGMT")
    wake_time = sleep_daily.get("sleepEndTimestampGMT")
    if bed_time: bed_time = datetime.datetime.fromtimestamp(bed_time/1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
    if wake_time: wake_time = datetime.datetime.fromtimestamp(wake_time/1000, tz=datetime.timezone.utc).astimezone(LOCAL_TZ)
    bb_values = body_battery[0].get("bodyBatteryValuesArray") if body_battery else []
    numeric_values = [v[1] for v in bb_values if isinstance(v,list) and v[1] is not None]
    body_battery_min = min(numeric_values) if numeric_values else None
    body_battery_max = max(numeric_values) if numeric_values else None
    body_weight = round(float(body_comp["dateWeightList"][0]["weight"])/453.592,2) if body_comp.get("dateWeightList") else None
    training_readiness = extract_value(readiness, ["score","trainingReadinessScore","unknown_0"]) or None

    # Training status (only yesterday)
    status_map = {3:"Recovery",6:"Strained"}
    current_status_val = extract_value(status, ["currentStatus","trainingStatus"])
    training_status_val = status_map.get(int(current_status_val)) if isinstance(current_status_val,(int,float)) else None

    stats = stats[0] if isinstance(stats,list) and stats else {}
    calories = safe_stat(stats, ["totalKilocalories","active_calories"])
    resting_hr = safe_stat(stats, ["restingHeartRate","heart_rate"])
    steps_total = sum(i.get("totalSteps",0) for i in steps) if steps else None

    health_props = {
        "Name": notion_title(yesterday.strftime("%m/%d/%Y")),
        "Date": notion_date(iso_yesterday),
        "Steps": notion_number(steps_total),
        "Body Weight": notion_number(body_weight),
        "Body Battery (Min)": notion_number(body_battery_min),
        "Body Battery (Max)": notion_number(body_battery_max),
        "Sleep Score": notion_number(sleep_score),
        "Bedtime": notion_date(bed_time),
        "Wake Time": notion_date(wake_time),
        "Training Readiness": notion_number(training_readiness),
        "Training Status": notion_select(training_status_val),
        "Resting HR": notion_number(resting_hr),
        "Calories Burned": notion_number(calories)
    }

    try:
        notion.pages.create(parent={"database_id":NOTION_HEALTH_DB_ID}, properties=health_props)
        logging.info(f"✅ Synced health metrics for {iso_yesterday}")
    except Exception as e:
        logging.error(f"⚠️ Failed to push health metrics: {e}")
        pprint.pprint(health_props)

    # ---------------------------
    # ACTIVITIES (recent + backfill attempt)
    # ---------------------------
    activities = safe_fetch(garmin.get_activities, 0, 50) or []
    logging.info(f"📤 Found {len(activities)} recent activities")

    training_effect_map = {0:"Unknown",1:"Recovery",2:"Aerobic Base",3:"Tempo",4:"Lactate Threshold",5:"Vo2Max",6:"Anaerobic Capacity"}

    for act in activities:
        act_date = act.get("startTimeLocal","")[:10] or iso_yesterday
        activity_name = act.get("activityName") or f"Activity {act_date}"
        distance_km = (act.get("distance") or 0)/1000
        duration_min = round((act.get("duration") or 0)/60,1)
        avg_pace_minkm = (duration_min/distance_km) if distance_km>0 else None
        distance_mi = distance_km * 0.621371
        avg_pace_mi = (duration_min/distance_mi) if distance_mi>0 else None
        training_effect = training_effect_map.get(act.get("trainingEffect",0),"Unknown")
        ae_effect = act.get("aeEffect",{}).get("value")
        an_effect = act.get("anEffect",{}).get("value")
        activity_type, _ = format_activity_type(act.get("activityType",{}).get("typeKey"), activity_name)

        activity_props = {
            "Date": notion_date(act_date),
            "Activity Name": notion_title(activity_name),
            "Distance (km)": notion_number(distance_km),
            "Distance (mi)": notion_number(distance_mi),
            "Duration (min)": notion_number(duration_min),
            "Avg Pace (min/km)": notion_text(f"{avg_pace_minkm:.2f}" if avg_pace_minkm else ""),
            "Avg Pace (min/mi)": notion_text(f"{avg_pace_mi:.2f}" if avg_pace_mi else ""),
            "Calories": notion_number(act.get("calories")),
            "Type": notion_select(activity_type),
            "Training Effect": notion_select(training_effect),
            "Aerobic": notion_number(ae_effect),
            "Anaerobic": notion_number(an_effect),
            "AE:AN": notion_number(ae_effect/an_effect if an_effect else None)
        }

        existing = activity_exists(notion, NOTION_ACTIVITIES_DB_ID, act_date, activity_type, activity_name)
        try:
            if existing:
                if needs_update(existing, activity_props):
                    notion.pages.update(page_id=existing['id'], properties=activity_props)
                    logging.info(f"🔄 Updated activity: {activity_name}")
            else:
                notion.pages.create(parent={"database_id":NOTION_ACTIVITIES_DB_ID}, properties=activity_props)
                logging.info(f"🏃 Logged activity: {activity_name}")
        except Exception as e:
            logging.error(f"⚠️ Failed to log activity {activity_name}: {e}")
            pprint.pprint(activity_props)

    garmin.logout()
    logging.info("🏁 Sync complete.")

if __name__=="__main__":
    main()

