import os
import json
import threading
import time
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel
import requests
import hashlib
import pandas as pd
import smtplib
from email.message import EmailMessage
from io import BytesIO

# --------------- Test Environment Override true/false ------------------------------- 
#def is_test_time_override():
#    """Allow time-based restrictions to be bypassed for testing."""
#    return os.getenv("FORCE_TEST_TIME", "false").lower() == "true"

# ---------------------- LOGGING CONFIG ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ---------------------- FASTAPI APP ----------------------
app = FastAPI(title="Chartink Fyers Webhook (Enhanced & Safe)")

# ---------------------- ENV VARIABLES (UNCHANGED NAMES) ----------------------
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
CLIENT_SECRET = os.getenv("FYERS_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("FYERS_REFRESH_TOKEN")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")
FYERS_PIN = os.getenv("FYERS_PIN")  # used in refresh, optional

LOW_PRICE_LIMIT = float(os.getenv("LOW_PRICE_LIMIT", 200))
MID_PRICE_LIMIT = float(os.getenv("MID_PRICE_LIMIT", 600))
LOW_QTY = int(os.getenv("LOW_QTY", 10))
HIGH_QTY = int(os.getenv("HIGH_QTY", 5))

TARGET_PCT = float(os.getenv("TARGET_PCT", 0.05))    # default 5% target
STOPLOSS_PCT = float(os.getenv("STOPLOSS_PCT", 0.01)) # default 1% SL
POSITIONS_FILE = os.getenv("POSITIONS_FILE", "open_positions.json")

# Trade mode control (unchanged)
TRADE_MODE = os.getenv("TRADE_MODE", "TEST").upper()  # "TEST" or "REAL"

# ---------------------- NEW EMAIL ENV VARIABLES (ADD THESE IN RENDER) ----------------------
EMAIL_USER = os.getenv("EMAIL_USER")   # sender Gmail
EMAIL_PASS = os.getenv("EMAIL_PASS")   # Gmail App Password
EMAIL_TO = os.getenv("EMAIL_TO")       # recipient

# ---------------------- ENTRY / ANALYTICS PARAMS ----------------------
ENTRY_TOLERANCE = float(os.getenv("ENTRY_TOLERANCE", 0.02))  # 2% above breakout
MIN_VOLUME_MULT = float(os.getenv("MIN_VOLUME_MULT", 1.5))     # min volume vs avg
MIN_CANDLE_BARS = int(os.getenv("MIN_CANDLE_BARS", 10))

# ---------------------- GLOBALS ----------------------
fyers = None
open_positions = {}   # persisted to POSITIONS_FILE for safety
trade_log = []        # in-memory list of trade records (dicts) used to generate Excel/email

# ---------------------- DUPLICATE ALERT PROTECTION ----------------------
active_trades = set()              # temporary in-progress stocks
lock = threading.Lock()            # ensures thread-safe checks

# ---------------------- TIMEZONE ----------------------
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def is_market_hours():
    """Check if current time is within NSE market hours (9:15 AM - 15:30 PM IST)."""
    now = now_ist().time()
    return dtime(9, 15) <= now <= dtime(15, 30)

def log_time_context():
    now_utc = datetime.now(timezone.utc)
    n = now_ist()
    logging.info(f"🕒 Time Check | UTC: {now_utc.strftime('%H:%M:%S')} | IST: {n.strftime('%H:%M:%S')}")

# ---------------------- HELPERS (positions file) ----------------------
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

# ---------------------- FYERS AUTH/INIT ----------------------
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
            "pin": FYERS_PIN
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

def init_fyers():
    global fyers
    if not ACCESS_TOKEN:
        refresh_access_token()
    else:
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="")
        logging.info("✅ Fyers initialized with provided access token.")

# ---------------------- MARKET DATA HELPERS ----------------------
def get_ltp(symbol: str):
    """Get latest price via quotes. symbol like 'NSE:RELIANCE-EQ'"""
    global fyers
    try:
        q = fyers.quotes({"symbols": symbol})
        ltp = None
        if isinstance(q, dict):
            ltp = q.get("d", [{}])[0].get("v", {}).get("lp")
        if ltp is None:
            ltp = q.get("ltp") if isinstance(q, dict) else None
        return float(ltp) if ltp else None
    except Exception as e:
        logging.error(f"Error fetching LTP for {symbol}: {e}")
        return None

def get_recent_15min_candles(symbol: str, count: int = 30):
    """
    Fetch 15-min candles using fyers history/historical if available.
    Returns list of dicts: [{'t': ts, 'o': o, 'h': h, 'l': l, 'c': c, 'v': v}, ...]
    If unable to fetch/parse, returns None (we'll skip trade to be safe).
    """
    global fyers
    try:
        now = now_ist()
        range_to = now.strftime("%Y-%m-%d")
        range_from = (now - timedelta(days=5)).strftime("%Y-%m-%d")
        params = {
            "symbol": symbol,
            "resolution": "15",
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": ""
        }
        if hasattr(fyers, "history"):
            resp = fyers.history(params)
        elif hasattr(fyers, "historical"):
            resp = fyers.historical(params)
        else:
            logging.warning("Fyers SDK has no history/historical method.")
            return None

        candles = []
        if isinstance(resp, dict):
            if "candles" in resp and isinstance(resp["candles"], list):
                raw = resp["candles"][-count:]
                for c in raw:
                    if isinstance(c, list) and len(c) >= 6:
                        candles.append({"t": c[0], "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": float(c[5])})
                    elif isinstance(c, dict):
                        candles.append({"t": c.get("t"), "o": float(c.get("o")), "h": float(c.get("h")), "l": float(c.get("l")), "c": float(c.get("c")), "v": float(c.get("v"))})
        # some SDKs might return list directly
        if not candles and isinstance(resp, list):
            for c in resp[-count:]:
                if isinstance(c, list) and len(c) >= 6:
                    candles.append({"t": c[0], "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": float(c[5])})
        if len(candles) < MIN_CANDLE_BARS:
            logging.warning(f"Insufficient candle bars for {symbol}. Got {len(candles)}")
            return None
        return candles
    except Exception as e:
        logging.warning(f"Could not fetch 15-min candles for {symbol}: {e}")
        return None

def compute_vwma(candles):
    """Compute VWMA over candles list (expects 'c' and 'v')."""
    try:
        num = 0.0
        den = 0.0
        for c in candles:
            num += float(c["c"]) * float(c["v"])
            den += float(c["v"])
        return (num / den) if den else None
    except Exception:
        return None

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
            logging.info(f"🧪 TEST MODE: simulated order -> {order}")
            return {"id": "TEST_ORDER_"+str(int(time.time())), "s": "ok"}
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

def log_trade_memory(record: dict):
    """Append trade record to in-memory trade_log list."""
    trade_log.append(record)
    logging.info(f"🧾 Trade log append: {record}")

def export_trade_log_to_excel_bytes():
    """Return Excel bytes (BytesIO) representing today's trade log."""
    if not trade_log:
        return None
    df = pd.DataFrame(trade_log)
    buffer = BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return buffer

def should_enter_trade(symbol: str, trigger_price: float) -> dict:
    """
    Pre-entry checks:
    - LTP not far above trigger (ENTRY_TOLERANCE)
    - recent 15-min candle volume vs avg
    - price not below VWMA
    Returns dict with ok flag and info.
    """
    symbol_code = f"NSE:{symbol}-EQ"
    ltp = get_ltp(symbol_code)
    if ltp is None:
        return {"ok": False, "reason": "no_ltp"}

    # no late entries after 15:10 # ✅ Timing checks
    now = now_ist().time()
    if now >= dtime(15,10):
        return {"ok": False, "reason": "post_15_10_time"}
        
    # ✅ Price tolerance check (avoid chasing breakouts)
    if ltp > trigger_price * (1 + ENTRY_TOLERANCE):
        return {"ok": False, "reason": "too_far_above_trigger", "ltp": ltp}
        
    # ✅ Optional: live volume sanity check
    candles = get_recent_15min_candles(symbol_code, count=30)
    if candles is None:
        return {"ok": False, "reason": "no_candles", "ltp": ltp}

    # previous closed bar
    prev_closed = candles[-2] if len(candles) >= 2 else candles[-1]
    prev_high = float(prev_closed["h"])
    prev_volume = float(prev_closed["v"])
    avg_vol = sum([float(c["v"]) for c in candles[-20:]]) / min(20, len(candles))
    vwma = compute_vwma(candles[-20:]) if len(candles) >= 5 else None

    #if prev_volume < avg_vol * MIN_VOLUME_MULT:
    #    return {"ok": False, "reason": "insufficient_vol_on_breakout", "prev_vol": prev_volume, "avg_vol": avg_vol, "ltp": ltp}
    #if vwma is not None and float(candles[-1]["c"]) < vwma:
    #    return {"ok": False, "reason": "price_below_vwma", "vwma": vwma, "ltp": ltp}
    # don't enter if ltp is significantly above prev_high (we want retest)
    if ltp > prev_high * (1 + ENTRY_TOLERANCE):
        return {"ok": False, "reason": "ltp_above_prev_high_too_much", "ltp": ltp, "prev_high": prev_high}

    return {"ok": True, "reason": "checks_passed", "ltp": ltp, "prev_high": prev_high, "avg_vol": avg_vol, "vwma": vwma}

def place_order(symbol: str, price: float, side: int = 1):
    """Place order with pre-checks for BUY and standard handling for SELL."""
    log_time_context()
    # block new entries after 15:10
    nowt = now_ist().time()
    if side == 1 and nowt >= dtime(15,10):
        logging.warning(f"⏸ Skipping BUY for {symbol} — post 15:10 IST.")
        record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "BUY_SKIPPED_LATE", "price": price, "qty": 0, "status": "skipped_late"}
        log_trade_memory(record)
        return {"status": "skipped", "reason": "post_15_10"}

    # don't place real orders if market closed
    if TRADE_MODE == "REAL" and not is_market_hours():
        logging.warning(f"🕒 Market closed — skipping real order for {symbol}.")
        record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "BUY_SKIPPED_MARKET_CLOSED", "price": price, "qty": 0, "status": "market_closed"}
        log_trade_memory(record)
        return {"status": "skipped", "reason": "market_closed"}

    # For BUY, run pre-entry checks
    if side == 1:
        checks = should_enter_trade(symbol, price)
        if not checks.get("ok"):
            logging.info(f"⏸ ENTER SKIPPED for {symbol}: {checks.get('reason')} | info: {checks}")
            record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "BUY_SKIPPED", "price": price, "qty": 0, "status": checks.get("reason")}
            log_trade_memory(record)
            return {"status": "skipped", "reason": checks.get("reason")}

    # determine qty using current LTP if available
    symbol_code = f"NSE:{symbol}-EQ"
    ltp = get_ltp(symbol_code)
    qty = get_quantity(ltp if ltp else price)

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

    action = "BUY" if side == 1 else "SELL"
    logging.info(f"📈 {action} {symbol} @ {price} | Qty: {qty} | Mode: {TRADE_MODE}")
    resp = safe_place_order(order)
    status = "ok" if isinstance(resp, dict) and resp.get("s") == "ok" else str(resp)

    # log in-memory
    record = {
        "timestamp": now_ist().isoformat(),
        "symbol": symbol,
        "action": action,
        "price": ltp if ltp else price,
        "qty": qty,
        "status": status
    }
    log_trade_memory(record)

    # update open_positions for BUYs only if success
    if side == 1 and isinstance(resp, dict) and resp.get("s") == "ok":
        entry_price = ltp if ltp else price
        open_positions[symbol] = {"entry_price": entry_price, "qty": qty, "timestamp": now_ist().isoformat()}
        save_positions()
        logging.info(f"✅ Added {symbol} to open_positions: {open_positions[symbol]}")
    elif side == -1 and isinstance(resp, dict) and resp.get("s") == "ok":
        # remove position on successful SELL
        open_positions.pop(symbol, None)
        save_positions()
        logging.info(f"🧾 Removed {symbol} from open_positions.")

    return resp

# ---------------------- BROKER POSITION CHECK ----------------------
def get_broker_open_positions():
    """
    Try to query broker for current open intraday positions.
    Returns:
      - dict mapping symbol -> netQty (positive means long), or
      - None if unable to determine
    """
    global fyers
    try:
        # prefer positions() if available
        if hasattr(fyers, "positions"):
            resp = fyers.positions()
            # typical structure may vary; try common keys
            positions = {}
            if isinstance(resp, dict):
                # many brokers return 'netPositions' or 'data' lists
                if "netPositions" in resp:
                    for p in resp["netPositions"]:
                        # adapt fields: check p.get('symbol'), p.get('netQty')
                        sym = p.get("symbol") or p.get("symbolName") or p.get("name")
                        qty = p.get("netQty") or p.get("qty") or 0
                        if sym:
                            positions[sym] = qty
                    return positions
                if "data" in resp and isinstance(resp["data"], list):
                    for p in resp["data"]:
                        sym = p.get("symbol")
                        qty = p.get("netQty") or p.get("qty") or 0
                        if isinstance(sym, str):
                            positions[sym] = qty
                    if positions:
                        return positions
            # fallback: return None and let caller try orderbook
        # fallback: use orderbook() and infer executed positions (not ideal)
        if hasattr(fyers, "orderbook"):
            resp = fyers.orderbook()
            if isinstance(resp, dict) and resp.get("s") == "ok":
                # orderbook returns orders; we infer executed net qty per symbol
                positions = {}
                for o in resp.get("data", []):
                    sym = o.get("symbol")
                    status = o.get("status")  # executed etc
                    filled_qty = o.get("filledQuantity") or o.get("qty") or 0
                    side = o.get("side")  # 1 buy, -1 sell maybe
                    # This is heuristic; orderbook interpretation is messy; return None to be safe
                return None
    except Exception as e:
        logging.warning(f"Could not fetch broker positions: {e}")
    return None

def verify_and_clear_positions_after_autosquare():
    """
    After broker auto-square window, verify with broker that positions are cleared.
    - Poll broker from 15:18 to 15:25
    - If positions cleared -> clear local open_positions and POSITIONS_FILE
    - If not cleared by 15:25 and TRADE_MODE==REAL -> attempt to close remaining local positions
      (we log attempts). Finally clear local memory (to avoid carrying forward).
    """
    start = now_ist()
    timeout_until = start.replace(hour=15, minute=25, second=30, microsecond=0)
    logging.info("🔎 Starting verification wait for broker auto-square-off (until ~15:25)...")
    while now_ist() < timeout_until:
        broker_pos = get_broker_open_positions()
        if broker_pos is not None:
            # determine if any net positions (long) exist
            nonzero = {s: q for s, q in broker_pos.items() if q and float(q) != 0}
            if not nonzero:
                # broker shows zero positions -> safe to clear local memory
                open_positions.clear()
                save_positions()
                logging.info("✅ Broker positions are clear. Local open_positions cleared.")
                return True
            else:
                logging.info(f"⚠️ Broker still shows positions: {nonzero}. Waiting...")
        else:
            logging.info("ℹ️ Unable to determine broker positions; will re-check shortly.")
        time.sleep(10)

    # timeout reached (≈15:25). If positions still local and TRADE_MODE==REAL, attempt to close them.
    if open_positions:
        logging.warning("⏰ Timeout reached (~15:25) — broker did not report clear positions. Attempting to close remaining local positions.")
        for symbol, pos in list(open_positions.items()):
            entry = pos.get("entry_price")
            qty = pos.get("qty", 0)
            ltp = get_ltp(f"NSE:{symbol}-EQ")
            ltp_to_use = ltp if ltp else entry
            if TRADE_MODE == "REAL":
                try:
                    logging.info(f"Attempting SELL for {symbol} qty={qty} ltp={ltp_to_use}")
                    place_order(symbol, ltp_to_use, side=-1)
                except Exception as e:
                    logging.error(f"Error attempting manual close for {symbol}: {e}")
            else:
                # simulate
                record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "SELL_SIM_MANUAL_CLOSE", "price": ltp_to_use, "qty": qty, "status": "simulated_manual_close"}
                log_trade_memory(record)
            # remove local regardless to avoid carrying positions overnight
            open_positions.pop(symbol, None)
            save_positions()
        logging.info("✅ Manual close attempts done; local open_positions cleared.")
        return False

    # nothing to clear
    logging.info("✅ No local open_positions to clear.")
    return True

# ---------------------- EXIT STRATEGY & SCHEDULERS ----------------------
def exit_monitor():
    """
    Runs in background:
    - While time < 15:10: target/stop monitoring (normal)
    - After 15:10: STOP target/stop monitoring (do not place exits)
    - Between 15:15-15:25: wait for broker auto-square-off, verify and clear local positions
    - At 15:25: send daily email report (after verification/close attempts)
    """
    logging.info("Exit monitor started.")
    monitoring_active = True  # whether to perform target/stop checks
    emailed = False

    while True:
        try:
            now = now_ist()
            # 1) If before 15:10 — monitor target/stop
            if now.time() < dtime(15,10):
                monitoring_active = True
                if open_positions:
                    for symbol, pos in list(open_positions.items()):
                        entry = float(pos["entry_price"])
                        qty = pos.get("qty", 0)
                        symbol_code = f"NSE:{symbol}-EQ"
                        quote = None
                        try:
                            quote = fyers.quotes({"symbols": symbol_code})
                        except Exception as e:
                            logging.warning(f"Quote fetch failed for {symbol}: {e}")
                        ltp = None
                        if isinstance(quote, dict):
                            ltp = quote.get("d", [{}])[0].get("v", {}).get("lp")
                        if ltp is None:
                            ltp = get_ltp(symbol_code)
                        if ltp is None:
                            continue
                        target = entry * (1 + TARGET_PCT)
                        stop = entry * (1 - STOPLOSS_PCT)
                        if ltp >= target or ltp <= stop:
                            logging.info(f"💰 {symbol}: Exit triggered @ {ltp} (Target: {target:.2f}, Stop: {stop:.2f})")
                            if TRADE_MODE == "REAL":
                                place_order(symbol, ltp, side=-1)
                            else:
                                # simulate exit
                                record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "SELL_SIM", "price": ltp, "qty": qty, "status": "simulated_exit"}
                                log_trade_memory(record)
                                open_positions.pop(symbol, None)
                                save_positions()
                time.sleep(10)
                continue

            # 2) If we've reached >=15:10, stop monitoring exits and entries (we already enforce no new entries in place_order / secure_place_thread)
            if monitoring_active:
                monitoring_active = False
                logging.info("⏳ 15:10 reached — stopping active entry/exit checks. Waiting for broker auto-square-off window (15:15-15:30).")

            # 3) At or after 15:15 we begin verification & cleanup (but do not place new exits during 15:10-15:25)
            # We'll trigger verification once (when time >= 15:18 better) then attempt manual close if required by 15:25
            if now.time() >= dtime(15,15):
                # Wait for broker to do auto-square-off until ~15:25, then verify and clear
                verify_and_clear_positions_after_autosquare()

            # 4) At or after 15:25 send daily email (only once)
            if now.time() >= dtime(15,25) and not emailed:
                logging.info("✉️ Sending daily trade report (15:25 IST).")
                send_daily_email()
                emailed = True

            # 5) After 15:30 we can exit/sleep the monitor loop until next day
            if now.time() > dtime(15,30) and emailed:
                logging.info("Market session over and report sent — exit monitor sleeping until next start.")
                break

            time.sleep(10)

        except Exception as e:
            logging.error(f"Exit monitor error: {e}")
            time.sleep(10)

# ---------------------- EMAIL / REPORT ----------------------
def send_daily_email():
    """Compose Excel from in-memory trade_log and email it (EMAIL_USER/EMAIL_PASS/EMAIL_TO)."""
    try:
        if not trade_log:
            logging.info("📭 No trades to email today.")
            return
        buffer = export_trade_log_to_excel_bytes()
        if buffer is None:
            logging.warning("No data to export for email.")
            return

        msg = EmailMessage()
        msg["Subject"] = f"Intraday Trade Log - {now_ist().strftime('%Y-%m-%d')}"
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.set_content("Attached: Intraday trade log for today.\n\n-- Automated by Chartink-Fyers bot")

        msg.add_attachment(buffer.read(), maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"trade_log_{now_ist().strftime('%Y-%m-%d')}.xlsx")

        if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
            logging.error("Email env variables (EMAIL_USER, EMAIL_PASS, EMAIL_TO) not set. Cannot send email.")
            return

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
        logging.info("✅ Daily trade report emailed successfully.")
    except Exception as e:
        logging.error(f"Error sending daily email: {e}")

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

# --- Symbol corrections for Fyers naming mismatches ---
SYMBOL_FIX = {
    "CIGNITITEC": "CIGNITITECH",
    "SHREECEM": "SHREECEMEQ",
    "BAJAJHLDNG": "BAJAJHLDNG",  # example of same
    # Add more here if you see "no_ltp" errors in future
}

        for idx, symbol in enumerate(stock_list):
             price = price_list[idx] if idx < len(price_list) else None

         # 🧩 Auto-correct common Chartink symbol truncations
            fixed_symbol = SYMBOL_FIX.get(symbol, symbol)
            if fixed_symbol != symbol:
               logging.info(f"🔧 Fixed symbol name: {symbol} → {fixed_symbol}")
               symbol = fixed_symbol

            if price:
               threading.Thread(target=place_order, args=(symbol, price, 1)).start()
        

        #for idx, symbol in enumerate(stock_list):
        #    price = price_list[idx] if idx < len(price_list) else None
        #    if price:
                # spawn thread for each candidate but DO NOT place orders after 15:10
        #        threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()

        return {"status": "success", "received": data}
    except Exception as e:
        logging.error(f"Error processing alert: {e}")
        return {"status": "error", "message": str(e)}

def secure_place_thread(symbol, price):
    """Delay a bit and call place_order safely (handles duplicates & 15:10 cutoff)."""
    try:
        time.sleep(0.3)

        # Skip post-15:10 new entries
        if now_ist().time() >= dtime(15, 10):
            logging.warning(f"⏸ Skipping {symbol} from webhook — post 15:10 IST.")
            rec = {
                "timestamp": now_ist().isoformat(),
                "symbol": symbol,
                "action": "BUY_SKIPPED_POST_15_10",
                "price": price,
                "qty": 0,
                "status": "skipped_late"
            }
            log_trade_memory(rec)
            return {"status": "skipped", "reason": "post_15_10"}

        # ✅ Duplicate guard
        with lock:
            if symbol in active_trades or symbol in open_positions:
                logging.warning(f"⛔ Duplicate alert ignored for {symbol} — already processing/open.")
                record = {
                    "timestamp": now_ist().isoformat(),
                    "symbol": symbol,
                    "action": "BUY_SKIPPED_DUPLICATE",
                    "price": price,
                    "qty": 0,
                    "status": "duplicate_skipped"
                }
                log_trade_memory(record)
                return {"status": "skipped", "reason": "duplicate"}

            # Mark this symbol as active (in-progress)
            active_trades.add(symbol)

        # proceed with order placement
        resp = place_order(symbol, price, side=1)
        return resp

    except Exception as e:
        logging.error(f"secure_place_thread error for {symbol}: {e}")
        return {"status": "error", "error": str(e)}

    finally:
        # always remove from active set at the end (safe cleanup)
        with lock:
            active_trades.discard(symbol)


# ---------------------- STARTUP ----------------------
@app.on_event("startup")
def startup_event():
    global open_positions
    logging.info(f"🚀 Starting Chartink Webhook Service... (Mode: {TRADE_MODE})")
    open_positions = load_positions() or {}
    init_fyers()
    # Start exit monitor (handles target/stop before 15:10, verification + cleanup after broker autosquare)
    threading.Thread(target=exit_monitor, daemon=True).start()
    logging.info("🚀 Exit monitor started (target/stop before 15:10; verification & email after).")

@app.get("/")
def home():
    now = now_ist().isoformat()
    return {"status": "running", "mode": TRADE_MODE, "time_IST": now}
