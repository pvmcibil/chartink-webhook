from fastapi import FastAPI, Request, BackgroundTasks
from fyers_apiv3 import fyersModel
import json, os, datetime, asyncio, requests, threading, time

app = FastAPI()

# --- CONFIGURATION ---

CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET", "YOUR_CLIENT_SECRET")

LOCAL_TOKEN_FILE = r"C:\Users\Dell\Documents\access_token.json"
SERVER_TOKEN_FILE = "/etc/secrets/access_token.json"

# --- Load Tokens ---
def load_token_data():
    """Load access and refresh tokens from env or local file."""
    access_token = os.getenv("FYERS_ACCESS_TOKEN")
    refresh_token = os.getenv("FYERS_REFRESH_TOKEN")

    if access_token and refresh_token:
        print("✅ Loaded Fyers tokens from environment variables.")
        return access_token, refresh_token

    for path in [LOCAL_TOKEN_FILE, SERVER_TOKEN_FILE]:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
                if "access_token" in data and "refresh_token" in data:
                    print(f"✅ Loaded Fyers tokens from {path}")
                    return data["access_token"], data["refresh_token"]

    raise ValueError("❌ No valid Fyers tokens found. Please set env vars or JSON file.")

ACCESS_TOKEN, REFRESH_TOKEN = load_token_data()

# Initialize Fyers API
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, is_async=True)


# --- Token Refresh Logic ---
def refresh_fyers_token():
    """Refresh expired or daily access token using refresh token."""
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
            print("✅ Access token refreshed successfully at", datetime.datetime.now())

            # Optionally save to file
            with open("/tmp/access_token.json", "w") as f:
                json.dump(data, f)

            return new_token
        else:
            print("⚠️ Failed to refresh token:", data)
    except Exception as e:
        print("❌ Error refreshing token:", e)
    return None


# --- Background Token Refresh Thread ---
def auto_refresh_loop():
    """Run token refresh every 22 hours."""
    while True:
        time.sleep(22 * 3600)  # wait 22 hours
        refresh_fyers_token()

@app.on_event("startup")
def start_refresh_thread():
    """Start background refresh loop on app startup."""
    threading.Thread(target=auto_refresh_loop, daemon=True).start()
    print("🟢 Background token refresher started.")


# --- Helper Functions ---
def decide_action(payload: dict):
    text = (payload.get("scan_name", "") + " " + payload.get("alert_name", "")).lower()
    if any(word in text for word in ["bearish", "sell", "short"]):
        return "sell"
    return "buy"


def to_fyers_symbol(stock: str):
    stock = stock.strip().upper()
    if stock.endswith(("CE", "PE")):
        return f"NSE:{stock}"
    return f"NSE:{stock}-EQ"


async def place_order(action: str, fyers_symbol: str, price: float = 0):
    side = 1 if action == "buy" else -1
    order = {
        "symbol": fyers_symbol,
        "qty": 25,
        "type": 2,  # Market order
        "side": side,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "disclosedQty": 0,
        "validity": "DAY",
        "offlineOrder": "False",
    }
    try:
        resp = await fyers.place_order(order)
        print(f"[{datetime.datetime.now()}] {action.upper()} → {fyers_symbol} @ {price}")
        print("Fyers response:", resp)

        # Auto-refresh on token expiry
        if "token" in str(resp).lower():
            new_token = refresh_fyers_token()
            if new_token:
                fyers.token = new_token
                await fyers.place_order(order)
        return resp

    except Exception as e:
        print(f"❌ Order error for {fyers_symbol}: {e}")
        return {"error": str(e)}


@app.post("/chartink")
async def chartink_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handles Chartink webhook alerts."""
    data = await request.json()
    print(f"[{datetime.datetime.now()}] 📩 Received alert:", data)

    stocks_str = data.get("stocks", "")
    prices_str = data.get("trigger_prices", "")

    if not stocks_str:
        return {"error": "No stocks found in alert"}

    stock_list = [s.strip() for s in stocks_str.split(",") if s.strip()]
    price_list = [float(p.strip()) for p in prices_str.split(",") if p.strip()] if prices_str else []

    action = decide_action(data)
    while len(price_list) < len(stock_list):
        price_list.append(0.0)

    for stock, price in zip(stock_list, price_list):
        fyers_symbol = to_fyers_symbol(stock)
        background_tasks.add_task(place_order, action, fyers_symbol, price)

    return {"status": "received", "action": action, "stocks": stock_list, "time": str(datetime.datetime.now())}


@app.get("/refresh_token")
def manual_refresh():
    """Manual endpoint to trigger token refresh."""
    new_token = refresh_fyers_token()
    if new_token:
        return {"status": "ok", "new_access_token": new_token}
    return {"status": "failed"}


@app.get("/")
def home():
    return {"status": "Chartink → Fyers webhook active", "time": str(datetime.datetime.now())}
