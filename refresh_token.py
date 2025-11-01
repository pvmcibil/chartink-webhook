import os
import requests
import logging

FYERS_APP_ID = os.getenv("FYERS_APP_ID")
FYERS_APP_SECRET = os.getenv("FYERS_APP_SECRET")
FYERS_REFRESH_TOKEN = os.getenv("FYERS_REFRESH_TOKEN")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")

def refresh_fyers_token():
    url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
    data = {
        "appId": FYERS_APP_ID,
        "secretKey": FYERS_APP_SECRET,
        "refresh_token": FYERS_REFRESH_TOKEN
    }

    resp = requests.post(url, json=data)
    resp.raise_for_status()
    token_data = resp.json()

    if "access_token" not in token_data:
        raise Exception(f"Token refresh failed: {token_data}")

    new_access_token = token_data["access_token"]
    logging.info(f"✅ New access token fetched: {new_access_token[:20]}...")

    # Update Render environment variable
    render_url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars"
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}"}
    payload = [{"key": "FYERS_ACCESS_TOKEN", "value": new_access_token}]
    r = requests.put(render_url, headers=headers, json=payload)
    r.raise_for_status()
    logging.info("✅ Updated Render environment variable.")

if __name__ == "__main__":
    refresh_fyers_token()
