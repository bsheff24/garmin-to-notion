import os
from notion_client import Client
from dotenv import load_dotenv
from datetime import datetime

# Load .env variables
load_dotenv()

# Connect to Notion
notion = Client(auth=os.getenv("NOTION_TOKEN"))

# Use your Health Metrics DB ID
db_id = os.getenv("NOTION_HEALTH_DB_ID")

# Test data to insert
test_row = {
    "Date": {"date": {"start": datetime.today().isoformat()}},
    "Bodyweight": {"number": 180},
    "Sleep Score": {"number": 85},
    "Steps": {"number": 7500}
}

# Create a new page in the Health Metrics DB
response = notion.pages.create(
    parent={"database_id": db_id},
    properties=test_row
)

print("Test row created:", response["id"])
