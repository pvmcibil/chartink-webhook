import os
import json
import threading
import time
import logging
from datetime import datetime, time as dtime
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel
import requests
import hashlib

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

LOW_PRICE_LIMIT = float(os.getenv("LOW_PRICE_LIMIT", 200))
MID_PRICE_LIMIT = float(os.getenv("MID_PRICE_LIMIT", 600))
LOW_QTY = int(os.getenv("LOW_QTY", 10))
HIGH_QTY = int(os.getenv("HIGH_QTY", 5))

TARGET_PCT = 0.05
STOPLOSS_PCT = 0.01
POSITIONS_FILE = "open_positions.json"

# Trade mode control
TRADE_MODE = os.getenv("TRADE_MODE", "TEST").upper()  # "TEST" or "REAL"

# ---------------------- GLOBALS ----------------------
fyers = None
open_positions = {}

# ---------------------- HELPERS ----------------------
def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading positions file: {e}")
    return {}

def save_positions():
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(open_positions, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving positions: {e}")

def is_market_hours():
    """Check if current time is within NSE market hours (9:15 AM - 3:30 PM IST)."""
    now = datetime.now().time()
    return dtime(9, 15) <= now <= dtime(15, 30)

# ---------------------- TOKEN REFRESH ----------------------
def refresh_access_token():
    global ACCESS_TOKEN, fyers
    if not REFRESH_TOKEN or not CLIENT_ID or not CLIENT_SECRET:
        logging.error("⚠️ Missing Fyers credentials. Check your environment variables.")
        return None

    logging.info("🔄 Refreshing access token...")

    try:
        app_hash = hashlib.sha256(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).hexdigest()
        url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
        payload = {
            "grant_type": "refresh_token",
            "appIdHash": app_hash,
            "refresh_token": REFRESH_TOKEN,
            "pin": os.getenv("FYERS_PIN")
        }

        response = requests.post(url, json=payload, timeout=10)
        data = response.json()

        if "access_token" in data:
            ACCESS_TOKEN = data["access_token"]
            fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
            logging.info("✅ Access token refreshed successfully.")
            return ACCESS_TOKEN
        else:
            logging.error(f"❌ Token refresh failed: {data}")
            return None
    except Exception as e:
        logging.error(f"Error refreshing access token: {e}")
        return None

# ---------------------- FYERS INIT ----------------------
def init_fyers():
    global fyers
    if not ACCESS_TOKEN:
        refresh_access_token()
    else:
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
        logging.info("✅ Fyers initialized with provided access token.")

# ---------------------- ORDER LOGIC ----------------------
def get_quantity(price: float) -> int:
    if price <= LOW_PRICE_LIMIT:
        return LOW_QTY
    elif price <= MID_PRICE_LIMIT:
        return 10
    return HIGH_QTY

def safe_place_order(order):
    global fyers
    try:
        if TRADE_MODE != "REAL":
            logging.info(f"🧪 TEST MODE: Order not sent to Fyers → {order}")
            return {"id": "TEST_ORDER", "status": "simulated", "s": "ok"}

        resp = fyers.place_order(order)
        if isinstance(resp, dict) and resp.get("code") == -16:
            logging.warning("⚠️ Auth failed. Refreshing token...")
            refresh_access_token()
            time.sleep(2)
            fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
            resp = fyers.place_order(order)

        logging.info(f"📦 Order response: {resp}")
        return resp

    except Exception as e:
        logging.error(f"Order placement error: {e}")
        return {}

def place_order(symbol: str, price: float, side: int = 1):
    # 🕒 Prevent real orders during closed market hours
    if TRADE_MODE == "REAL" and not is_market_hours():
        logging.warning(f"🕒 Market closed — skipping real order for {symbol}.")
        return {"status": "skipped", "reason": "market_closed"}

    qty = get_quantity(price)
    symbol_code = f"NSE:{symbol}-EQ"

    order = {
        "symbol": symbol_code,
        "qty": qty,
        "type": 2,  # Market
        "side": side,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "validity": "DAY",
        "offlineOrder": False
    }

    logging.info(f"📈 {'BUY' if side == 1 else 'SELL'} {symbol} @ {price} | Qty: {qty} | Mode: {TRADE_MODE}")
    resp = safe_place_order(order)

    # ✅ Only save BUYs if successful
    if side == 1 and isinstance(resp, dict) and resp.get("s") == "ok":
        open_positions[symbol] = {
            "entry_price": price,
            "qty": qty,
            "timestamp": datetime.now().isoformat()
        }
        save_positions()
        logging.info(f"✅ Added {symbol} to open_positions.")
    elif side == 1:
        logging.warning(f"⚠️ {symbol}: Order not successful, not adding to open_positions. Response: {resp}")

    elif side == -1:
        open_positions.pop(symbol, None)
        save_positions()
        logging.info(f"🧾 Removed {symbol} from open_positions.")

    return resp

# ---------------------- EXIT STRATEGY ----------------------
def exit_monitor():
    while True:
        try:
            if not open_positions:
                time.sleep(10)
                continue

            if not is_market_hours():
                logging.info("🕒 Market closed — skipping exit checks.")
                time.sleep(60)
                continue

            for symbol, pos in list(open_positions.items()):
                entry = pos["entry_price"]
                qty = pos["qty"]
                symbol_code = f"NSE:{symbol}-EQ"
                quote = fyers.quotes({"symbols": symbol_code})
                ltp = quote.get("d", [{}])[0].get("v", {}).get("lp")

                if not ltp:
                    continue

                target = entry * (1 + TARGET_PCT)
                stop = entry * (1 - STOPLOSS_PCT)

                if ltp >= target or ltp <= stop:
                    msg = f"💰 {symbol}: Exit triggered @ {ltp} (Target: {target:.2f}, Stop: {stop:.2f})"
                    logging.info(msg)

                    if TRADE_MODE == "REAL":
                        place_order(symbol, ltp, side=-1)
                    else:
                        logging.info(f"🧪 TEST MODE: Skipping SELL order for {symbol}")

            time.sleep(15)

        except Exception as e:
            logging.error(f"Exit monitor error: {e}")
            time.sleep(15)

# ---------------------- WEBHOOK ----------------------
@app.post("/chartink")
async def chartink_alert(request: Request):
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
                threading.Thread(target=place_order, args=(symbol, price, 1)).start()

        return {"status": "success", "received": data}

    except Exception as e:
        logging.error(f"Error processing alert: {e}")
        return {"status": "error", "message": str(e)}

# ---------------------- STARTUP ----------------------
@app.on_event("startup")
def startup_event():
    global open_positions
    logging.info(f"🚀 Starting Chartink Webhook Service... (Mode: {TRADE_MODE})")
    open_positions = load_positions()
    init_fyers()
    threading.Thread(target=exit_monitor, daemon=True).start()
    logging.info("🚀 Exit monitor started.")

@app.get("/")
def home():
    return {"status": "running", "mode": TRADE_MODE, "time": datetime.now().isoformat()}
