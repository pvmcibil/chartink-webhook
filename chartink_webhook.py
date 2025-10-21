"""
chartink_webhook.py
-------------------
Production-ready FastAPI app for Chartink → Fyers order automation.
✅ Handles multiple stocks in a single Chartink alert
✅ Places dynamic-sized buy orders in Fyers
✅ Auto-refreshes Fyers token daily (in background)
✅ Fully environment-configurable
"""

from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel
import os
import json
import asyncio
import requests
import threading
import time

app = FastAPI(title="Chartink → Fyers Webhook")

# ------------------ CONFIGURATION ------------------

# Required Fyers credentials
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("FYERS_REFRESH_TOKEN")

if not all([CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, REFRESH_TOKEN]):
    raise ValueError("❌ Missing one or more required environment variables in Render!")

# Quantity and pricing rules (configurable)
LOW_PRICE_LIMIT = float(os.getenv("LOW_PRICE_LIMIT", 200))
MID_PRICE_LIMIT = float(os.getenv("MID_PRICE_LIMIT", 600))
LOW_QTY = int(os.getenv("LOW_QTY", 10))
HIGH_QTY = int(os.getenv("HIGH_QTY", 5))

print(f"⚙️ Quantity Logic: price ≤ {MID_PRICE_LIMIT} → {LOW_QTY}, above {MID_PRICE_LIMIT} → {HIGH_QTY}")

# Initialize Fyers
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, is_async=True)


# ------------------ TOKEN REFRESH ------------------
def refresh_fyers_token():
    """Refresh Fyers access token using refresh token (valid 30 days)."""
    print("🔄 Refreshing Fyers access token...")

    url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
    payload = {
        "grant_type": "refresh_token",
        "appIdHash": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }

    try:
        res = requests.post(url, json=payload, timeout=10)
        data = res.json()

        if "access_token" in data:
            new_token = data["access_token"]
            os.environ["FYERS_ACCESS_TOKEN"] = new_token
            fyers.token = new_token
            print("✅ Fyers access token refreshed successfully.")
            return new_token
        else:
            print("⚠️ Failed to refresh token:", data)
            return None

    except Exception as e:
        print("❌ Error refreshing token:", e)
        return None


def background_token_refresh():
    """Runs in background to refresh token every ~23 hours."""
    while True:
        refresh_fyers_token()
        time.sleep(23 * 3600)


@app.on_event("startup")
def start_background():
    """Start token refresher when app launches."""
    threading.Thread(target=background_token_refresh, daemon=True).start()
    print("🚀 Background token refresh started.")


# ------------------ ORDER LOGIC ------------------
async def place_fyers_order(symbol: str, price: float):
    """
    Places MARKET buy order with dynamic quantity based on price.
    Quantity thresholds are environment-configurable.
    """
    try:
        full_symbol = f"NSE:{symbol}-EQ"

        # --- Flexible quantity logic ---
        if price <= LOW_PRICE_LIMIT:
            qty = LOW_QTY
        elif price <= MID_PRICE_LIMIT:
            qty = LOW_QTY
        else:
            qty = HIGH_QTY

        order = {
            "symbol": full_symbol,
            "qty": qty,
            "type": 2,            # MARKET
            "side": 1,            # BUY
            "productType": "INTRADAY",
            "limitPrice": 0,
            "stopPrice": 0,
            "disclosedQty": 0,
            "validity": "DAY",
            "offlineOrder": "False"
        }

        response = await fyers.place_order(order)
        print(f"✅ Order placed for {full_symbol} @ {price} (Qty: {qty}) → {response}")
        return {"symbol": symbol, "price": price, "qty": qty, "response": response}

    except Exception as e:
        print(f"❌ Error placing order for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


# ------------------ WEBHOOK ------------------
@app.post("/chartink")
async def chartink_webhook(request: Request):
    """
    Webhook endpoint for Chartink alerts.
    Handles multiple stocks, places orders concurrently.
    """
    try:
        data = await request.json()
        print(f"📩 Received alert: {data}")

        stocks = data.get("stocks", "")
        prices = data.get("trigger_prices", "")
        if not stocks:
            return {"status": "error", "message": "No stocks in alert"}

        stock_list = [s.strip() for s in stocks.split(",")]
        price_list = [float(p) for p in prices.split(",")] if prices else []

        # Place all orders concurrently
        tasks = []
        for i, symbol in enumerate(stock_list):
            price = price_list[i] if i < len(price_list) else 0.0
            tasks.append(place_fyers_order(symbol, price))

        results = await asyncio.gather(*tasks)
        return {"status": "ok", "results": results}

    except Exception as e:
        print("❌ Error processing alert:", e)
        return {"status": "error", "message": str(e)}


# ------------------ HEALTH ------------------
@app.get("/")
def root():
    return {"status": "running", "message": "Chartink → Fyers webhook active ✅"}


@app.get("/refresh_token")
def manual_refresh():
    """Manually trigger token refresh (optional)."""
    new_token = refresh_fyers_token()
    return {"status": "ok" if new_token else "failed"}


# ------------------ LOCAL TEST ------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("chartink_webhook:app", host="0.0.0.0", port=8000)
