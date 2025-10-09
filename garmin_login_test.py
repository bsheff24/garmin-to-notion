import requests

username = 'brysoncsheffield@gmail.com'
password = 'Hottytoddy96*'

login_url = 'https://sso.garmin.com/sso/login'
payload = {
    'username': username,
    'password': password,
    'embed': 'true',
    'gauthHost': 'https://sso.garmin.com/sso',
    'service': 'https://connect.garmin.com/modern'
}

session = requests.Session()
response = session.post(login_url, data=payload)

if response.status_code == 200:
    print("✅ Login successful!")
else:
    print(f"❌ Login failed with status code {response.status_code}")
