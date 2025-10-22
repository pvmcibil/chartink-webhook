import os
import json
import threading
import time
import logging
from datetime import datetime
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel
import requests

# ---------------------- LOGGING CONFIG ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ---------------------- FASTAPI APP ----------------------
app = FastAPI(title="Chartink Fyers Webhook")

# ---------------------- ENV VARIABLES ----------------------
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("FYERS_REFRESH_TOKEN")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")

# Quantity logic
LOW_PRICE_LIMIT = float(os.getenv("LOW_PRICE_LIMIT", 200))
MID_PRICE_LIMIT = float(os.getenv("MID_PRICE_LIMIT", 600))
LOW_QTY = int(os.getenv("LOW_QTY", 10))
HIGH_QTY = int(os.getenv("HIGH_QTY", 5))

# Trading constants
TARGET_PCT = 5 / 100      # 5% target
STOPLOSS_PCT = 1 / 100    # 1% stop loss

# Token file (optional, used in local dev)
TOKEN_FILE = "access_token.json"

# ---------------------- FYERS SETUP ----------------------
fyers = None
open_positions = {}  # track active trades for exit strategy


def refresh_access_token():
    """Refresh access token using refresh_token"""
    global ACCESS_TOKEN, fyers

    if not REFRESH_TOKEN:
        logging.warning("⚠️ Missing FYERS_REFRESH_TOKEN. Please set it in Render environment.")
        return None

    try:
        url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
        payload = {
            "grant_type": "refresh_token",
            "appId": CLIENT_ID,
            "secret_key": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN
        }
        response = requests.post(url, json=payload)
        data = response.json()

        if data.get("access_token"):
            ACCESS_TOKEN = data["access_token"]
            logging.info("🔄 Access token refreshed successfully.")

            fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
            return ACCESS_TOKEN
        else:
            logging.error(f"❌ Failed to refresh token: {data}")
    except Exception as e:
        logging.error(f"Error refreshing token: {e}")
    return None


def init_fyers():
    """Initialize fyers client"""
    global fyers
    if not ACCESS_TOKEN:
        refresh_access_token()
    else:
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
        logging.info("✅ Fyers initialized with existing access token.")


# ---------------------- ORDER LOGIC ----------------------
def determine_quantity(price: float) -> int:
    """Decide quantity based on price brackets"""
    if price <= LOW_PRICE_LIMIT:
        return LOW_QTY
    elif price <= MID_PRICE_LIMIT:
        return 10  # middle band, optional customization
    else:
        return HIGH_QTY


def place_fyers_order(symbol: str, price: float):
    """Place Buy Order"""
    try:
        qty = determine_quantity(price)
        symbol_code = f"NSE:{symbol}-EQ"

        order = {
            "symbol": symbol_code,
            "qty": qty,
            "type": 2,  # Limit order
            "side": 1,  # Buy
            "productType": "INTRADAY",
            "limitPrice": price,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopPrice": 0,
            "takeProfit": 0,
            "stopLoss": 0
        }

        logging.info(f"📈 Placing Buy Order: {symbol_code} @ {price} | Qty: {qty}")
        response = fyers.place_order(order)
        logging.info(f"✅ Order Response: {response}")

        # Store for exit strategy
        if "id" in response:
            open_positions[symbol] = {
                "entry_price": price,
                "qty": qty,
                "timestamp": datetime.now().isoformat()
            }

    except Exception as e:
        logging.error(f"❌ Error placing order for {symbol}: {e}")


# ---------------------- EXIT STRATEGY ----------------------
def exit_monitor():
    """Monitor open positions and auto-exit based on target/SL"""
    while True:
        try:
            if not open_positions:
                time.sleep(10)
                continue

            for symbol, pos in list(open_positions.items()):
                entry_price = pos["entry_price"]
                qty = pos["qty"]
                target_price = entry_price * (1 + TARGET_PCT)
                stop_price = entry_price * (1 - STOPLOSS_PCT)

                symbol_code = f"NSE:{symbol}-EQ"
                quote = fyers.quotes({"symbols": symbol_code})
                ltp = quote.get("d", [{}])[0].get("v", {}).get("lp")

                if not ltp:
                    continue

                if ltp >= target_price or ltp <= stop_price:
                    side = -1  # Sell
                    order = {
                        "symbol": symbol_code,
                        "qty": qty,
                        "type": 2,
                        "side": 2,
                        "productType": "INTRADAY",
                        "limitPrice": ltp,
                        "validity": "DAY",
                        "offlineOrder": False
                    }
                    fyers.place_order(order)
                    logging.info(f"💰 Exited {symbol} @ {ltp} | Target: {target_price:.2f}, SL: {stop_price:.2f}")
                    open_positions.pop(symbol, None)
            time.sleep(15)
        except Exception as e:
            logging.error(f"Exit monitor error: {e}")
            time.sleep(15)


# ---------------------- WEBHOOK ENDPOINT ----------------------
@app.post("/chartink")
async def chartink_alert(request: Request):
    """Receive Chartink webhook alert"""
    try:
        data = await request.json()
        logging.info(f"📩 Received alert: {data}")

        stocks = data.get("stocks", "")
        trigger_prices = data.get("trigger_prices", "")
        if not stocks:
            return {"status": "error", "message": "No stocks found"}

        stock_list = [s.strip() for s in stocks.split(",")]
        price_list = [float(p.strip()) for p in trigger_prices.split(",") if p.strip()]

        for idx, symbol in enumerate(stock_list):
            price = price_list[idx] if idx < len(price_list) else None
            if price:
                threading.Thread(target=place_fyers_order, args=(symbol, price)).start()

        return {"status": "success", "received": data}

    except Exception as e:
        logging.error(f"Error processing alert: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------- STARTUP ----------------------
@app.on_event("startup")
def startup_event():
    logging.info("🚀 Starting Chartink Webhook Service...")
    init_fyers()
    threading.Thread(target=exit_monitor, daemon=True).start()
    logging.info("🚀 Exit monitor thread started.")


@app.get("/")
def home():
    return {"status": "running", "time": datetime.now().isoformat()}
