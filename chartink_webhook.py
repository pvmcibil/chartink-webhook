import os
import json
import threading
import time
import requests
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel

# ==========================
# 🔧 ENVIRONMENT VARIABLES
# ==========================
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET")
REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("FYERS_REFRESH_TOKEN")
TOKEN_FILE = os.getenv("FYERS_ACCESS_TOKEN")
POSITIONS_FILE = "open_positions.json"

# ==========================
# ⚙️ HELPER FUNCTIONS
# ==========================

def get_access_token():
    """Load access token from file"""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                token_data = json.load(f)
                return token_data.get("access_token")
        except Exception as e:
            print(f"⚠️ Token read error: {e}")
    return None


def save_access_token(token):
    """Save access token locally"""
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": token}, f)


def refresh_access_token():
    """Refresh Fyers access token using refresh token"""
    print("🔄 Refreshing access token...")
    try:
        url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
        payload = {
            "grant_type": "refresh_token",
            "appId": CLIENT_ID,
            "secret_key": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            access_token = data.get("access_token")
            if access_token:
                save_access_token(access_token)
                print("✅ Token refreshed successfully")
                return access_token
        print(f"❌ Token refresh failed: {response.text}")
    except Exception as e:
        print(f"⚠️ Token refresh error: {e}")
    return None

def load_positions():
    """Load open positions from JSON file"""
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_positions():
    """Persist open positions to disk"""
    with open(POSITIONS_FILE, "w") as f:
        json.dump(open_positions, f, indent=2)


# ==========================
# ⚙️ INITIALIZE FYERS
# ==========================
access_token = get_access_token() or refresh_access_token()

if not access_token:
    print("⚠️ Missing refresh_token. Please reauthenticate.")

fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=access_token, log_path="")

# ==========================
# ⚙️ FASTAPI SETUP
# ==========================
app = FastAPI()

# ==========================
# 📦 POSITION MEMORY
# ==========================
open_positions = load_positions()

# ==========================
# 📊 DYNAMIC QUANTITY LOGIC
# ==========================
def get_quantity(price):
    if price < 200:
        return 10
    elif price < 600:
        return 10
    else:
        return 5


# ==========================
# 🔧 FYERS UTILITIES
# ==========================
def get_ltp(symbol):
    """Fetch LTP from Fyers"""
    try:
        data = fyers.quotes({"symbols": symbol})
        return float(data["d"][0]["v"]["lp"])
    except Exception as e:
        print(f"⚠️ LTP fetch error for {symbol}: {e}")
        return 0.0


def safe_place_order(order):
    """Place order safely with retry on token expiry"""
    global fyers, access_token
    try:
        resp = fyers.place_order(order)
        if resp.get("s") == "ok":
            print(f"✅ Order placed: {resp}")
            return True
        elif "Invalid token" in str(resp):
            print("⚠️ Token expired. Refreshing...")
            access_token = refresh_access_token()
            if access_token:
                fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=access_token, log_path="")
                resp = fyers.place_order(order)
                if resp.get("s") == "ok":
                    print(f"✅ Order placed after refresh: {resp}")
                    return True
        print(f"❌ Order failed: {resp}")
    except Exception as e:
        print(f"⚠️ Order exception: {e}")
    return False


# ==========================
# 🛒 PLACE ORDER
# ==========================
def place_order(symbol, price, side=1):
    qty = get_quantity(price)
    order = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,           # Market Order
        "side": side,        # 1=BUY, -1=SELL
        "productType": "INTRADAY",
        "limitPrice": 0,
        "validity": "DAY",
        "offlineOrder": False
    }
    return safe_place_order(order)


# ==========================
# 🎯 EXIT MONITOR
# ==========================
def exit_monitor():
    print("🚀 Exit monitor thread started.")
    global open_positions
    while True:
        try:
            time.sleep(10)
            for pos in open_positions[:]:
                symbol = pos["symbol"]
                buy_price = pos["buy_price"]
                target = pos["target"]
                stop = pos["stop"]

                ltp = get_ltp(symbol)
                if not ltp:
                    continue

                if ltp >= target or ltp <= stop:
                    print(f"💰 Exit condition met for {symbol}: LTP={ltp}")
                    success = place_order(symbol, ltp, side=-1)
                    if success:
                        print(f"✅ Square-off successful for {symbol} at ₹{ltp}")
                        open_positions.remove(pos)
                        save_positions()
        except Exception as e:
            print(f"⚠️ Exit monitor error: {e}")
            time.sleep(5)


# Start background thread
threading.Thread(target=exit_monitor, daemon=True).start()

# ==========================
# 📩 WEBHOOK ENDPOINT
# ==========================
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        stocks = data.get("stocks")
        trigger_prices = data.get("trigger_prices")

        if not stocks:
            return {"error": "No stocks received"}

        symbols = [s.strip() for s in stocks.split(",")]
        prices = [float(p.strip()) for p in trigger_prices.split(",")] if trigger_prices else [0.0] * len(symbols)

        results = []
        for i, symbol in enumerate(symbols):
            price = prices[i] if i < len(prices) else 0.0
            qty = get_quantity(price)
            print(f"🚀 Buy signal received: {symbol} @ ₹{price} (qty={qty})")

            if place_order(symbol, price, side=1):
                target = round(price * 1.05, 2)
                stop = round(price * 0.99, 2)
                open_positions.append({"symbol": symbol, "buy_price": price, "target": target, "stop": stop})
                save_positions()
                results.append({"symbol": symbol, "status": "Order placed"})
            else:
                results.append({"symbol": symbol, "status": "Order failed"})

        return {"result": results}

    except Exception as e:
        print(f"⚠️ Webhook error: {e}")
        return {"error": str(e)}


# ==========================
# 🚀 STARTUP LOG
# ==========================
print("🚀 Starting Chartink Webhook Service...")
if not access_token:
    print("❌ No access token found.")
else:
    print("✅ Service ready to receive Chartink alerts.")
