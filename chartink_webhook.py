import os
import json
import time
import threading
import requests
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel

# ======================================================
# 🔧 CONFIGURATION
# ======================================================

# Fyers App Credentials (stored as Render environment variables)
FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
FYERS_CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET")
FYERS_APP_ID_TYPE = "2"

# Token file path (you uploaded this)
TOKEN_FILE = "access_token.json"

# Target and Stoploss percentages
TARGET_PCT = float(os.getenv("TARGET_PCT", 5))   # 5% target
STOPLOSS_PCT = float(os.getenv("STOPLOSS_PCT", 1))  # 1% stoploss

# Fyers API endpoints
FYERS_API_BASE = "https://api-t1.fyers.in/api/v3"

# ======================================================
# 🧩 INITIALIZATION
# ======================================================

app = FastAPI()
fyers = None
open_positions = {}  # store trades being tracked


# ======================================================
# 🔐 FYERS AUTHENTICATION HANDLING
# ======================================================

def load_tokens():
    """Load tokens from JSON file."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    """Save refreshed tokens."""
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=4)

def refresh_access_token():
    """Refresh access token using refresh_token."""
    tokens = load_tokens()
    if not tokens.get("refresh_token"):
        print("⚠️ Missing refresh_token. Please reauthenticate.")
        return

    data = {
        "grant_type": "refresh_token",
        "appIdHash": FYERS_CLIENT_ID,
        "refresh_token": tokens["refresh_token"],
    }

    try:
        res = requests.post(f"{FYERS_API_BASE}/generate_token", json=data).json()
        if "access_token" in res:
            print("🔄 Access token refreshed.")
            tokens["access_token"] = res["access_token"]
            save_tokens(tokens)
        else:
            print(f"❌ Token refresh failed: {res}")
    except Exception as e:
        print(f"⚠️ Exception during token refresh: {e}")

def get_fyers_client():
    """Return a ready Fyers client instance."""
    global fyers
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    if not access_token:
        print("❌ No access token found.")
        return None

    fyers = fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, token=access_token)
    return fyers


# ======================================================
# 📩 CHARTINK WEBHOOK HANDLER
# ======================================================

@app.post("/chartink")
async def chartink_alert(request: Request):
    """Receive Chartink alerts and place orders in Fyers."""
    payload = await request.json()
    print(f"📩 Received alert: {payload}")

    stocks = payload.get("stocks", "")
    trigger_prices = payload.get("trigger_prices", "")
    stock_list = [s.strip().upper() for s in stocks.split(",") if s.strip()]
    price_list = [float(p.strip()) for p in trigger_prices.split(",") if p.strip()]

    for i, stock in enumerate(stock_list):
        price = price_list[i] if i < len(price_list) else None
        if price:
            place_buy_order(stock, price)

    return {"status": "success", "received": payload}


# ======================================================
# 💰 ORDER PLACEMENT & MANAGEMENT
# ======================================================

def place_buy_order(symbol: str, price: float):
    """Place a market BUY order and register for exit monitoring."""
    fyers_client = get_fyers_client()
    if not fyers_client:
        print("⚠️ Fyers client not ready.")
        return

    # Convert to Fyers format (NSE:STOCK)
    fyers_symbol = f"NSE:{symbol}-EQ"

    # Decide quantity based on price
    qty = 10 if price <= 200 else 5

    # Build order
    order_data = {
        "symbol": fyers_symbol,
        "qty": qty,
        "type": 2,       # Market order
        "side": 1,       # Buy
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "offlineOrder": "False"
    }

    try:
        response = fyers_client.place_order(order_data)
        print(f"✅ Buy order placed: {response}")

        if "id" in response:
            order_id = response["id"]
            open_positions[symbol] = {
                "order_id": order_id,
                "entry_price": price,
                "qty": qty,
                "target": price * (1 + TARGET_PCT / 100),
                "stoploss": price * (1 - STOPLOSS_PCT / 100),
                "symbol": fyers_symbol
            }
    except Exception as e:
        print(f"❌ Error placing buy order for {symbol}: {e}")


def place_sell_order(symbol: str, qty: int):
    """Place a SELL order for exit."""
    fyers_client = get_fyers_client()
    if not fyers_client:
        print("⚠️ Fyers client not ready for sell order.")
        return

    fyers_symbol = f"NSE:{symbol}-EQ"
    order_data = {
        "symbol": fyers_symbol,
        "qty": qty,
        "type": 2,       # Market
        "side": -1,      # Sell
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "offlineOrder": "False"
    }

    try:
        response = fyers_client.place_order(order_data)
        print(f"💰 Exit order placed for {symbol}: {response}")
    except Exception as e:
        print(f"❌ Error placing exit order for {symbol}: {e}")


# ======================================================
# 📊 EXIT MONITORING (Target / Stoploss)
# ======================================================

def get_ltp(symbol: str) -> float:
    """Get last traded price for symbol."""
    fyers_client = get_fyers_client()
    if not fyers_client:
        return 0.0
    try:
        data = fyers_client.quotes({"symbols": f"NSE:{symbol}-EQ"})
        return float(data["d"][0]["v"]["lp"])
    except Exception as e:
        print(f"⚠️ Failed to fetch LTP for {symbol}: {e}")
        return 0.0

def monitor_exits():
    """Background thread to monitor all open trades."""
    print("🚀 Exit monitor thread started.")
    while True:
        try:
            for sym, trade in list(open_positions.items()):
                current = get_ltp(sym)
                entry = trade["entry_price"]
                target = trade["target"]
                stoploss = trade["stoploss"]
                qty = trade["qty"]

                if current >= target:
                    print(f"🎯 {sym} hit target {target}, current {current}")
                    place_sell_order(sym, qty)
                    del open_positions[sym]

                elif current <= stoploss:
                    print(f"🛑 {sym} hit stoploss {stoploss}, current {current}")
                    place_sell_order(sym, qty)
                    del open_positions[sym]

            time.sleep(60)  # check every minute
        except Exception as e:
            print(f"⚠️ Exit monitor error: {e}")
            time.sleep(60)


# ======================================================
# 🚀 STARTUP EVENTS
# ======================================================

@app.on_event("startup")
def startup_event():
    """Initialize Fyers client and start background thread."""
    print("🚀 Starting Chartink Webhook Service...")
    refresh_access_token()
    get_fyers_client()
    threading.Thread(target=monitor_exits, daemon=True).start()

# ======================================================
# ✅ END
# ======================================================
