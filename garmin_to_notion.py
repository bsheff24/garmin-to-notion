import os
import datetime
import logging
import pprint
from garminconnect import Garmin

# ---------------------------
# ENV VARIABLES
# ---------------------------
GARMIN_USERNAME = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------
# CLIENT
# ---------------------------
logging.info("🔐 Logging into Garmin...")
garmin = Garmin(GARMIN_USERNAME, GARMIN_PASSWORD)
garmin.login()

# ---------------------------
# DATE SETUP
# ---------------------------
today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.isoformat()
logging.info(f"📅 Debugging Garmin data for {yesterday_str}")

# ---------------------------
# FETCH DATA
# ---------------------------
def safe_fetch(func, *args):
    try:
        return func(*args)
    except Exception as e:
        logging.warning(f"⚠️ {func.__name__} unavailable: {e}")
        return None

activities = safe_fetch(garmin.get_activities, 0, 10)
steps = safe_fetch(garmin.get_daily_steps, yesterday_str, yesterday_str)
sleep_data = safe_fetch(garmin.get_sleep_data, yesterday_str)
body_battery = safe_fetch(garmin.get_body_battery, yesterday_str, yesterday_str)
body_comp = safe_fetch(garmin.get_body_composition, yesterday_str)
readiness = safe_fetch(garmin.get_training_readiness, yesterday_str)
training_status = safe_fetch(garmin.get_training_status, yesterday_str)
stats = safe_fetch(garmin.get_stats_and_body, yesterday_str)

# ---------------------------
# DEBUG PRINT
# ---------------------------
logging.info("\n🔹 RAW GARMIN DATA DUMP 🔹\n")

logging.info("📌 Activities:")
pprint.pprint(activities)

logging.info("\n📌 Steps:")
pprint.pprint(steps)

logging.info("\n📌 Sleep Data:")
pprint.pprint(sleep_data)

logging.info("\n📌 Body Battery:")
pprint.pprint(body_battery)

logging.info("\n📌 Body Composition:")
pprint.pprint(body_comp)

logging.info("\n📌 Training Readiness:")
pprint.pprint(readiness)

logging.info("\n📌 Training Status:")
pprint.pprint(training_status)

logging.info("\n📌 Stats & Body:")
pprint.pprint(stats)

# ---------------------------
# LOGOUT
# ---------------------------
garmin.logout()
logging.info("\n🏁 Garmin debug dump complete.")
