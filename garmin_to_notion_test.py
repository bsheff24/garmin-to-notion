# garmin_to_notion_test.py
from dotenv import load_dotenv
import os
from notion_client import Client
from garminconnect import Garmin
import datetime

# Load environment variables
load_dotenv()

# === Notion Setup ===
notion_token = os.getenv("NOTION_TOKEN")
health_db_id = os.getenv("NOTION_HEALTH_DB_ID")
activities_db_id = os.getenv("NOTION_ACTIVITIES_DB_ID")

print("Notion token loaded:", notion_token is not None)

notion = Client(auth=notion_token)

# Test Notion connection
try:
    health_test = notion.databases.query(database_id=health_db_id, page_size=1)
    activities_test = notion.databases.query(database_id=activities_db_id, page_size=1)
    print("✅ Notion DBs connected successfully!")
except Exception as e:
    print("❌ Notion connection failed:", e)

# === Garmin Setup ===
garmin_username = os.getenv("GARMIN_USERNAME")
garmin_password = os.getenv("GARMIN_PASSWORD")

client = Garmin(garmin_username, garmin_password, prompt_mfa=True)

try:
    client.login()
    print("✅ Garmin login successful!")
except Exception as e:
    print("❌ Garmin login failed:", e)

# === Quick Data Pull Test ===
today = datetime.date.today().strftime("%Y-%m-%d")
try:
    stats = client.get_stats(today)
    print("Today's Garmin stats:", stats)
except Exception as e:
    print("❌ Garmin stats fetch failed:", e)
