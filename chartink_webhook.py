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

TARGET_PCT = float(os.getenv("TARGET_PCT", 0.02))    # default 2% target
STOPLOSS_PCT = float(os.getenv("STOPLOSS_PCT", 0.01)) # default 1% percent fallback SL
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

# ---------------------- NEW: POSITION / STOP CONFIG (added — you can set these in Render) -------------
# Per-script capital allocation (position value cap) — you asked for 20,000
CAPITAL_PER_TRADE = float(os.getenv("CAPITAL_PER_TRADE", 20000))  # ₹20,000 default
# Max % of CAPITAL_PER_TRADE you're willing to lose on one trade (used to compute qty)
RISK_PCT = float(os.getenv("RISK_PCT", 0.01))  # 1% of the per-trade capital (default = ₹200)

# Technical stop method config
STOP_METHOD = os.getenv("STOP_METHOD", "ATR").upper()   # "ATR" | "SWING_LOW" | "VWMA" | "PERCENT"
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.5))
SWING_LOOKBACK = int(os.getenv("SWING_LOOKBACK", 5))
VWMA_LOOKBACK = int(os.getenv("VWMA_LOOKBACK", 20))
# -----------------------------------------------------------------------------------------------------

# ---------------------- GLOBALS ----------------------
fyers = None
open_positions = {}   # persisted to POSITIONS_FILE for safety
trade_log = []        # in-memory list of trade records (dicts) used to generate Excel/email

# ---------------------- DUPLICATE ALERT PROTECTION ----------------------
active_trades = set()              # temporary in-progress stocks
lock = threading.Lock()            # ensures thread-safe checks

# ---------------------- SYMBOL RESOLUTION (fixes + cache) ----------------------
# Manual fixes for common Chartink truncation / mismatches
SYMBOL_FIX = {
    "CIGNITITEC": "CIGNITITECH",
    "SHREECEM": "SHREECEM",
    # add more entries as you find "no_ltp" logs
}

# Cache resolved symbol_code for the session to reduce repeated lookups
# maps incoming normalized -> resolved code (or None if unresolvable)
SYMBOL_CACHE = {}

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
    """Get latest price via Fyers quotes API (v3). symbol like 'NSE:RELIANCE-EQ'"""
    global fyers
    try:
        if fyers is None:
            logging.warning("Fyers client not initialized for get_ltp.")
            return None

        q = fyers.quotes({"symbols": symbol})
        if not isinstance(q, dict):
            logging.debug(f"Unexpected quotes response for {symbol}: {q}")
            return None

        # check for 's' field (success flag)
        if q.get("s") == "error":
            msg = q.get("message") or q.get("msg")
            logging.debug(f"Invalid symbol {symbol}: {msg}")
            return None

        # Fyers returns list under 'd'
        if "d" in q and isinstance(q["d"], list) and q["d"]:
            item = q["d"][0]
            v = item.get("v", {}) if isinstance(item, dict) else {}
            ltp = v.get("lp") or v.get("ltp") or v.get("last_price")
            if ltp:
                return float(ltp)

        # Fallback for old style or empty d[]
        if "ltp" in q:
            return float(q["ltp"])

        logging.debug(f"No LTP found in quotes response for {symbol}: {q}")
        return None

    except Exception as e:
        logging.debug(f"Error fetching LTP for {symbol}: {e}")
        return None

def get_recent_15min_candles(symbol: str, count: int = 30):
    """
    Fetch 15-min candles using fyers history/historical if available.
    symbol should be Fyers symbol format e.g. 'NSE:RELIANCE-EQ' or 'RELIANCE' depending on SDK.
    """
    global fyers
    try:
        if fyers is None:
            logging.warning("Fyers client not initialized for candles.")
            return None
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

# ---------------------- TECHNICAL STOP HELPERS (NEW) ----------------------
def compute_true_ranges(candles):
    """Return list of true ranges for a list of candles (expects dicts with o,h,l,c)."""
    trs = []
    prev_close = None
    for c in candles:
        h = float(c["h"])
        l = float(c["l"])
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = float(c["c"])
    return trs

def compute_atr(candles, period=14):
    """
    Compute ATR using last `period` candles (candles ordered oldest->newest).
    Returns ATR float or None.
    """
    if not candles or len(candles) < 2:
        return None
    # use last `period+1` candles to compute TRs then SMA of TRs for ATR (simple ATR)
    use = candles[-(period+1):] if len(candles) >= period+1 else candles
    trs = compute_true_ranges(use)
    if not trs:
        return None
    # take last `period` TRs if available
    last_trs = trs[-period:] if len(trs) >= period else trs
    return sum(last_trs) / len(last_trs) if last_trs else None

def compute_swing_low(candles, lookback=5):
    """
    Return the swing low (minimum low) over the previous `lookback` *closed* candles.
    Expects candles oldest->newest and excludes the currently forming candle by default.
    """
    if not candles:
        return None
    # use previous `lookback` fully closed candles: skip last bar if it's still current
    use = candles[:-1] if len(candles) > 1 else candles
    use = use[-lookback:] if len(use) >= lookback else use
    lows = [float(c["l"]) for c in use if "l" in c]
    return min(lows) if lows else None

def compute_technical_stop(entry_price: float, symbol_code: str):
    """
    Compute stop based on STOP_METHOD. Returns price (stop) or None.
    For LONG trades only.
    """
    try:
        candles = get_recent_15min_candles(symbol_code, count=30)
        if not candles:
            return None

        method = STOP_METHOD
        if method == "ATR":
            atr = compute_atr(candles, period=ATR_PERIOD)
            if atr:
                # compute stop as entry - ATR * multiplier
                stop = entry_price - (atr * ATR_MULT)
                # minimal buffer (0.2% floor) to avoid zero distance
                min_dist = entry_price * 0.002
                if entry_price - stop < min_dist:
                    stop = entry_price - min_dist
                return round(stop, 2)

        if method == "SWING_LOW":
            swing = compute_swing_low(candles, lookback=SWING_LOOKBACK)
            if swing:
                buffer = entry_price * 0.002  # small buffer below swing low
                stop = swing - buffer
                if stop >= entry_price:
                    stop = entry_price * (1 - STOPLOSS_PCT)
                return round(stop, 2)

        if method == "VWMA":
            vwma = compute_vwma(candles[-VWMA_LOOKBACK:]) if len(candles) >= VWMA_LOOKBACK else compute_vwma(candles)
            if vwma:
                stop = min(entry_price * (1 - STOPLOSS_PCT), vwma * 0.995)  # slightly below VWMA
                return round(stop, 2)

        # fallback to percent-based stop
        return round(entry_price * (1 - STOPLOSS_PCT), 2)

    except Exception as e:
        logging.debug(f"compute_technical_stop error for {symbol_code}: {e}")
        return round(entry_price * (1 - STOPLOSS_PCT), 2)

# ---------------------- SYMBOL RESOLUTION FUNCTIONS ----------------------
def _normalize_incoming_symbol(symbol: str) -> str:
    """Strip possible prefixes/suffixes Chartink might include."""
    s = symbol.strip().upper()
    s = s.replace("NSE:", "").replace("BSE:", "").replace("-EQ", "").strip()
    return s

def resolve_symbol_code(symbol: str):
    """
    Resolve Chartink symbol (like SBIN) to valid Fyers symbol code (like NSE:SBIN-EQ).
    Uses multiple attempts and caching.
    """
    if not symbol:
        return None

    base_raw = _normalize_incoming_symbol(symbol)
    base = SYMBOL_FIX.get(base_raw, base_raw)

    # check cache
    cached = SYMBOL_CACHE.get(base_raw) or SYMBOL_CACHE.get(base)
    if cached is not None:
        return cached

    candidates = [
        f"NSE:{base}-EQ",
        f"NSE:{base}",
        f"BSE:{base}"
    ]

    for code in candidates:
        ltp = get_ltp(code)
        if ltp is not None:
            SYMBOL_CACHE[base_raw] = code
            SYMBOL_CACHE[base] = code
            logging.info(f"✅ Resolved symbol '{symbol}' → {code} (ltp={ltp})")
            return code
        else:
            logging.debug(f"❌ Candidate invalid: {code}")

    # last-ditch: check using Fyers symbols API
    try:
        url = "https://api-t1.fyers.in/api/v3/symbols"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            symbols_data = resp.json()
            if isinstance(symbols_data, list):
                match = next((s for s in symbols_data if s.get("symbol").startswith("NSE:") and base in s.get("symbol")), None)
                if match:
                    resolved = match.get("symbol")
                    SYMBOL_CACHE[base_raw] = resolved
                    logging.info(f"✅ Resolved via /symbols lookup: {symbol} -> {resolved}")
                    return resolved
    except Exception as e:
        logging.debug(f"Fyers symbols lookup failed for {symbol}: {e}")

    SYMBOL_CACHE[base_raw] = None
    SYMBOL_CACHE[base] = None
    logging.warning(f"❌ Could not resolve symbol '{symbol}' to a valid Fyers code.")
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
    - Resolve symbol_code
    - LTP not far above trigger (ENTRY_TOLERANCE)
    - recent 15-min candle volume vs avg
    - price not below VWMA
    Returns dict with ok flag and info.
    """
    symbol_code = resolve_symbol_code(symbol)
    if symbol_code is None:
        return {"ok": False, "reason": "no_ltp"}

    ltp = get_ltp(symbol_code)
    if ltp is None:
        return {"ok": False, "reason": "no_ltp"}

    # no late entries after 15:10
    now = now_ist().time()
    if now >= dtime(15,10):
        return {"ok": False, "reason": "post_15_10_time"}

    # Price tolerance check (avoid chasing breakouts)
    if ltp > trigger_price * (1 + ENTRY_TOLERANCE):
        return {"ok": False, "reason": "too_far_above_trigger", "ltp": ltp}

    # Live volume sanity check using 15m candles
    candles = get_recent_15min_candles(symbol_code, count=30)
    if candles is None:
        return {"ok": False, "reason": "no_candles", "ltp": ltp}

    # previous closed bar
    prev_closed = candles[-2] if len(candles) >= 2 else candles[-1]
    prev_high = float(prev_closed["h"])
    prev_volume = float(prev_closed["v"])
    avg_vol = sum([float(c["v"]) for c in candles[-20:]]) / min(20, len(candles))
    vwma = compute_vwma(candles[-20:]) if len(candles) >= 5 else None

    # don't enter if ltp is significantly above prev_high (we want retest)
    if ltp > prev_high * (1 + ENTRY_TOLERANCE):
        return {"ok": False, "reason": "ltp_above_prev_high_too_much", "ltp": ltp, "prev_high": prev_high}

    return {"ok": True, "reason": "checks_passed", "ltp": ltp, "prev_high": prev_high, "avg_vol": avg_vol, "vwma": vwma, "symbol_code": symbol_code}

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
        # we get resolved symbol_code from checks
        symbol_code = checks.get("symbol_code")
    else:
        # for SELL use resolved symbol too
        symbol_code = resolve_symbol_code(symbol)
        if symbol_code is None:
            logging.warning(f"Could not resolve symbol for SELL: {symbol}")
            record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "SELL_SKIPPED_NO_SYMBOL", "price": price, "qty": 0, "status": "no_symbol"}
            log_trade_memory(record)
            return {"status": "skipped", "reason": "no_symbol"}

    # determine qty using current LTP if available AND apply CAPITAL_PER_TRADE + technical stop sizing
    ltp = get_ltp(symbol_code)
    qty = None
    computed_tech_stop = None

    if ltp:
        try:
            # compute technical stop using recent candles
            tech_stop = compute_technical_stop(ltp, symbol_code)
            # fallback to percent if technical stop not available
            if tech_stop is None:
                tech_stop = round(ltp * (1 - STOPLOSS_PCT), 2)

            # ensure stop is below entry
            if tech_stop >= ltp:
                tech_stop = round(ltp * (1 - STOPLOSS_PCT), 2)

            risk_per_share = ltp - tech_stop
            # safety: don't allow zero or negative risk_per_share
            if risk_per_share <= 0:
                raise ValueError("Invalid risk per share computed")

            max_loss = CAPITAL_PER_TRADE * RISK_PCT
            qty_risk_based = max(1, int(max_loss / risk_per_share))
            qty_value_based = max(1, int(CAPITAL_PER_TRADE / ltp))

            # choose the safer (smaller) qty so both exposure cap and risk cap hold
            qty = min(qty_risk_based, qty_value_based)
            computed_tech_stop = tech_stop

            logging.info(
                f"Sizing for {symbol}: LTP={ltp:.2f}, tech_stop={tech_stop:.2f}, "
                f"risk/share={risk_per_share:.2f}, qty_risk_based={qty_risk_based}, qty_value_based={qty_value_based}, chosen_qty={qty}"
            )
        except Exception as e:
            logging.warning(f"Could not compute position size for {symbol} via technical stop: {e}. Falling back to get_quantity().")
            qty = get_quantity(ltp if ltp else price)
    else:
        # ltp not available — fallback to existing rule
        qty = get_quantity(price)

    # Ensure qty at least 1
    qty = max(1, int(qty))

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
    logging.info(f"📈 {action} {symbol} (code={symbol_code}) @ {price} | Qty: {qty} | Mode: {TRADE_MODE}")
    resp = safe_place_order(order)
    status = "ok" if isinstance(resp, dict) and resp.get("s") == "ok" else str(resp)

    # log in-memory
    record = {
        "timestamp": now_ist().isoformat(),
        "symbol": symbol,
        "symbol_code": symbol_code,
        "action": action,
        "price": ltp if ltp else price,
        "qty": qty,
        "status": status
    }
    log_trade_memory(record)

    # update open_positions for BUYs only if success (store technical stop)
    if side == 1 and isinstance(resp, dict) and resp.get("s") == "ok":
        entry_price = ltp if ltp else price
        # compute or re-use technical stop; keep conservative fallback to percent
        try:
            tstop = computed_tech_stop if computed_tech_stop else compute_technical_stop(entry_price, symbol_code)
        except Exception:
            tstop = None
        if tstop is None:
            tstop = round(entry_price * (1 - STOPLOSS_PCT), 2)

        open_positions[symbol] = {
            "entry_price": entry_price,
            "qty": qty,
            "timestamp": now_ist().isoformat(),
            "stop_price": tstop
        }
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
    Returns a dict mapping symbol -> netQty or None if unavailable.
    """
    global fyers
    try:
        if hasattr(fyers, "positions"):
            resp = fyers.positions()
            positions = {}
            if isinstance(resp, dict):
                if "netPositions" in resp:
                    for p in resp["netPositions"]:
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
        # fallback: try orderbook (less reliable)
        if hasattr(fyers, "orderbook"):
            resp = fyers.orderbook()
            # not implementing complex inference here; return None for safety
            return None
    except Exception as e:
        logging.warning(f"Could not fetch broker positions: {e}")
    return None

def verify_and_clear_positions_after_autosquare():
    """
    After broker auto-square window, verify with broker that positions are cleared.
    Polls broker until ~15:25 IST and clears local positions if broker is clear.
    """
    start = now_ist()
    timeout_until = start.replace(hour=15, minute=25, second=30, microsecond=0)
    logging.info("🔎 Starting verification wait for broker auto-square-off (until ~15:25)...")
    while now_ist() < timeout_until:
        broker_pos = get_broker_open_positions()
        if broker_pos is not None:
            nonzero = {s: q for s, q in broker_pos.items() if q and float(q) != 0}
            if not nonzero:
                open_positions.clear()
                save_positions()
                logging.info("✅ Broker positions are clear. Local open_positions cleared.")
                return True
            else:
                logging.info(f"⚠️ Broker still shows positions: {nonzero}. Waiting...")
        else:
            logging.info("ℹ️ Unable to determine broker positions; will re-check shortly.")
        time.sleep(10)

    # timeout reached
    if open_positions:
        logging.warning("⏰ Timeout reached (~15:25) — broker did not report clear positions. Attempting to close remaining local positions.")
        for symbol, pos in list(open_positions.items()):
            entry = pos.get("entry_price")
            qty = pos.get("qty", 0)
            symbol_code = resolve_symbol_code(symbol)
            ltp = get_ltp(symbol_code) if symbol_code else None
            ltp_to_use = ltp if ltp else entry
            if TRADE_MODE == "REAL" and symbol_code:
                try:
                    logging.info(f"Attempting SELL for {symbol} qty={qty} ltp={ltp_to_use}")
                    place_order(symbol, ltp_to_use, side=-1)
                except Exception as e:
                    logging.error(f"Error attempting manual close for {symbol}: {e}")
            else:
                record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "SELL_SIM_MANUAL_CLOSE", "price": ltp_to_use, "qty": qty, "status": "simulated_manual_close"}
                log_trade_memory(record)
            open_positions.pop(symbol, None)
            save_positions()
        logging.info("✅ Manual close attempts done; local open_positions cleared.")
        return False

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
    monitoring_active = True
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
                        symbol_code = resolve_symbol_code(symbol)
                        if not symbol_code:
                            continue
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

                        # prefer stored technical stop if present
                        stored_stop = None
                        try:
                            stored_stop = float(pos.get("stop_price")) if pos.get("stop_price") else None
                        except Exception:
                            stored_stop = None

                        if stored_stop is None:
                            stop = entry * (1 - STOPLOSS_PCT)
                        else:
                            stop = stored_stop

                        if ltp >= target or ltp <= stop:
                            logging.info(f"💰 {symbol}: Exit triggered @ {ltp} (Target: {target:.2f}, Stop: {stop:.2f})")
                            if TRADE_MODE == "REAL":
                                place_order(symbol, ltp, side=-1)
                            else:
                                record = {"timestamp": now_ist().isoformat(), "symbol": symbol, "action": "SELL_SIM", "price": ltp, "qty": qty, "status": "simulated_exit"}
                                log_trade_memory(record)
                                open_positions.pop(symbol, None)
                                save_positions()
                time.sleep(10)
                continue

            # 2) Stop monitoring after 15:10
            if monitoring_active:
                monitoring_active = False
                logging.info("⏳ 15:10 reached — stopping active entry/exit checks. Waiting for broker auto-square-off window (15:15-15:30).")

            # 3) At or after 15:15 begin verification & cleanup
            if now.time() >= dtime(15,15):
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

        for idx, symbol in enumerate(stock_list):
            price = price_list[idx] if idx < len(price_list) else None
            if price:
                # use secure_place_thread which enforces duplicate checks and time-cutoff
                threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()

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

        # Duplicate guard (use raw symbol key for duplicates)
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

            # Mark this symbol as active
            active_trades.add(symbol)

        # proceed with order placement
        resp = place_order(symbol, price, side=1)
        return resp

    except Exception as e:
        logging.error(f"secure_place_thread error for {symbol}: {e}")
        return {"status": "error", "error": str(e)}

    finally:
        with lock:
            active_trades.discard(symbol)

# ---------------------- STARTUP ----------------------
@app.on_event("startup")
def startup_event():
    global open_positions
    logging.info(f"🚀 Starting Chartink Webhook Service... (Mode: {TRADE_MODE})")
    open_positions = load_positions() or {}
    init_fyers()
    threading.Thread(target=exit_monitor, daemon=True).start()
    logging.info("🚀 Exit monitor started (target/stop before 15:10; verification & email after).")

@app.get("/")
def home():
    now = now_ist().isoformat()
    return {"status": "running", "mode": TRADE_MODE, "time_IST": now}
