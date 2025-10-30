#!/usr/bin/env python3
"""
Render-safe trading bot with ATR-based SL/Target, TEST/REAL mode,
in-memory positions, and daily Excel + email summary.

Author: venu madhav (2025) — updated
"""

import os
import sys
import time
import json
import threading
import logging
import datetime as dt
import tempfile

import pandas as pd
import numpy as np
from fastapi import FastAPI, Request
from fyers_apiv3 import fyersModel
import uvicorn
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# ---------------- CONFIGURATION ----------------
FYERS_ID = os.getenv("FYERS_CLIENT_ID", "")
FYERS_SECRET = os.getenv("FYERS_CLIENT_SECRET", "")
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "https://www.google.com")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "")

EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

TRADE_MODE = os.getenv("TRADE_MODE", "TEST").upper()  # TEST or REAL
STOP_METHOD = os.getenv("STOP_METHOD", "ATR")
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.5))
ATR_TARGET_MULT = float(os.getenv("ATR_TARGET_MULT", 2.0))
TARGET_PCT = float(os.getenv("TARGET_PCT", 1.5))
SL_PCT = float(os.getenv("SL_PCT", 0.8))

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ---------------- GLOBALS ----------------
app = FastAPI()
fyers = None
open_positions = {}  # In-memory positions
lock = threading.Lock()


# ---------------- HELPERS ----------------
def now_ist():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)


def _normalize_for_fyers(symbol: str) -> str:
    """Return a candidate Fyers symbol (best-effort).
    If already contains ':', assume user supplied a resolved symbol.
    Otherwise prepend NSE: and -EQ (common for eq stocks).
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    if ":" in s:
        return s
    # attempt common default
    return f"NSE:{s}-EQ"


def get_atr(df: pd.DataFrame, period: int = 14):
    """Compute ATR (SMA of TR) from a dataframe with high/low/close columns.
    Returns float or None.
    """
    try:
        if df is None or df.shape[0] < max(3, period + 1):
            return None
        # ensure numeric
        df = df.copy()
        for col in ["high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["high", "low", "close"], inplace=True)
        if df.shape[0] < period:
            return None
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else None
    except Exception as e:
        logging.debug(f"get_atr error: {e}")
        return None


def fetch_ohlc(symbol: str, interval: str = "15", lookback_days: int = 7):
    """Fetch historical candles from Fyers (candles list format).
    Returns DataFrame with columns: ts, open, high, low, close, vol or None.
    """
    try:
        if fyers is None:
            logging.debug("fetch_ohlc: fyers not initialized.")
            return None
        now = now_ist()
        params = {
            "symbol": symbol,
            "resolution": interval,
            "date_format": "1",
            "range_from": (now - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d"),
            "range_to": now.strftime("%Y-%m-%d"),
            "cont_flag": "1"
        }
        resp = fyers.history(params)
        if not resp:
            return None
        # Fyers returns "candles" as list of lists: [ts, o, h, l, c, v]
        candles = resp.get("candles") if isinstance(resp, dict) else None
        if not candles:
            return None
        df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "vol"])
        # convert numeric columns
        for c in ["open", "high", "low", "close", "vol"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        return df
    except Exception as e:
        logging.warning(f"fetch_ohlc failed for {symbol}: {e}")
        return None


def get_ltp(symbol: str):
    """Get latest price via Fyers quotes API. symbol should be fyers symbol format."""
    try:
        if fyers is None:
            logging.debug("get_ltp: fyers not initialized.")
            return None
        q = fyers.quotes({"symbols": symbol})
        if not isinstance(q, dict):
            return None
        # parse common response structure
        if "d" in q and isinstance(q["d"], list) and q["d"]:
            item = q["d"][0]
            v = item.get("v", {}) if isinstance(item, dict) else {}
            ltp = v.get("lp") or v.get("ltp") or v.get("last_price")
            if ltp is not None:
                return float(ltp)
        if "ltp" in q:
            return float(q["ltp"])
        return None
    except Exception as e:
        logging.debug(f"LTP error for {symbol}: {e}")
        return None


# ---------------- TRADING CORE ----------------
def calculate_sl_tgt(entry: float, atr: float):
    """Return (stop_loss, target) rounded to 2 decimals."""
    try:
        if atr and STOP_METHOD == "ATR":
            sl = entry - (ATR_MULT * atr)
            tgt = entry + (ATR_TARGET_MULT * atr)
        else:
            sl = entry * (1 - SL_PCT / 100)
            tgt = entry * (1 + TARGET_PCT / 100)
        return round(sl, 2), round(tgt, 2)
    except Exception as e:
        logging.debug(f"calculate_sl_tgt error: {e}")
        return round(entry * (1 - SL_PCT / 100), 2), round(entry * (1 + TARGET_PCT / 100), 2)


def place_order(symbol: str, price: float, qty: int, side: str):
    """Place order or simulate based on TRADE_MODE."""
    try:
        if TRADE_MODE == "REAL":
            order = {
                "symbol": symbol,
                "qty": qty,
                "type": 2,
                "side": 1 if side == "BUY" else -1,
                "productType": "INTRADAY",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY"
            }
            resp = fyers.place_order(order)
            logging.info(f"REAL {side} placed for {symbol}: {resp}")
            return resp
        else:
            logging.info(f"TEST {side} simulated for {symbol} @ {price} (qty={qty})")
            return {"s": "ok", "mode": "TEST_SIM"}
    except Exception as e:
        logging.error(f"Order failed {symbol}: {e}")
        return {"s": "error", "error": str(e)}


def secure_place_thread(symbol: str, price: float):
    """Thread-safe entry flow: normalize symbol, compute ATR stop/target, store position and place buy (or simulate)."""
    try:
        # Normalize to fyers symbol format if needed
        resolved = _normalize_for_fyers(symbol)
        if not resolved:
            logging.warning(f"Invalid symbol provided: {symbol}")
            return

        with lock:
            # avoid duplicates
            key = symbol.strip().upper()
            if key in open_positions:
                logging.info(f"Duplicate {key} ignored (already open).")
                return

            # fetch recent candles and compute ATR
            df = fetch_ohlc(resolved, interval="15", lookback_days=7)
            atr = get_atr(df, ATR_PERIOD) if df is not None else None

            sl, tgt = calculate_sl_tgt(price, atr)
            qty = 1  # default; you can enhance sizing later

            open_positions[key] = {
                "symbol": key,
                "fyers_symbol": resolved,
                "entry_price": float(price),
                "atr": atr,
                "stop_loss": sl,
                "target": tgt,
                "qty": qty,
                "timestamp": now_ist().isoformat(),
                "status": f"{TRADE_MODE}_OPEN",
            }

        # Place order outside lock (network call)
        place_order(resolved, price, qty, "BUY")
        logging.info(f"{key} opened @ {price}, SL {sl}, TGT {tgt}")
    except Exception as e:
        logging.error(f"secure_place_thread error for {symbol}: {e}", exc_info=True)


# ---------------- EXIT MONITOR ----------------
def monitor_exits():
    """Continuously monitor open_positions for SL/TGT hits using live LTP."""
    logging.info("Exit monitor running.")
    while True:
        try:
            # create snapshot to iterate safely
            with lock:
                snapshot = dict(open_positions)

            for key, pos in snapshot.items():
                try:
                    # skip already closed
                    if pos.get("status", "").startswith(f"{TRADE_MODE}_EXIT"):
                        continue

                    fyers_sym = pos.get("fyers_symbol") or _normalize_for_fyers(key)
                    ltp = get_ltp(fyers_sym)
                    if ltp is None:
                        continue

                    # read values
                    sl = float(pos.get("stop_loss"))
                    tgt = float(pos.get("target"))
                    qty = int(pos.get("qty", 1))

                    if ltp <= sl:
                        # SL hit - place SELL
                        place_order(fyers_sym, ltp, qty, "SELL")
                        with lock:
                            open_positions[key].update({
                                "exit_price": float(ltp),
                                "exit_reason": "SL_HIT",
                                "status": f"{TRADE_MODE}_EXIT_SL",
                                "exit_timestamp": now_ist().isoformat()
                            })
                        logging.info(f"{key} SL hit @ {ltp}")
                    elif ltp >= tgt:
                        # Target hit - place SELL
                        place_order(fyers_sym, ltp, qty, "SELL")
                        with lock:
                            open_positions[key].update({
                                "exit_price": float(ltp),
                                "exit_reason": "TGT_HIT",
                                "status": f"{TRADE_MODE}_EXIT_TGT",
                                "exit_timestamp": now_ist().isoformat()
                            })
                        logging.info(f"{key} TGT hit @ {ltp}")
                except Exception as e:
                    logging.debug(f"monitor_exits inner error for {key}: {e}", exc_info=True)

        except Exception as e:
            logging.warning(f"monitor_exits error: {e}", exc_info=True)

        time.sleep(15)  # poll every 15 seconds


# ---------------- EMAIL SUMMARY ----------------
def email_summary():
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        logging.warning("Email credentials not configured.")
        return
    try:
        with lock:
            df = pd.DataFrame(open_positions).T

        if df.empty:
            logging.info("No trades today to email.")
            return

        df["P&L"] = np.where(
            df["status"].str.contains("EXIT"),
            (df["exit_price"].astype(float) - df["entry_price"].astype(float)) * df["qty"].astype(float),
            0,
        )

        xfile = os.path.join(tempfile.gettempdir(), "daily_report.xlsx")
        df.to_excel(xfile, index=False)

        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = f"Daily Trade Report {now_ist().date()}"
        msg.attach(MIMEText("Attached daily trade summary.", "plain"))
        with open(xfile, "rb") as f:
            msg.attach(MIMEApplication(f.read(), Name="daily_report.xlsx"))

        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASS)
            s.send_message(msg)

        logging.info("Daily report email sent.")
    except Exception as e:
        logging.error(f"Email send failed: {e}", exc_info=True)


# ---------------- API ENDPOINTS ----------------
@app.get("/")
def home():
    return {"message": "Chartink Webhook is running ✅", "mode": TRADE_MODE}


@app.post("/chartink")
async def chartink_webhook(request: Request):
    """Accept Chartink (or custom) payloads. Handles CSV lists in 'stocks' and
    'trigger_prices' as well as single 'symbol'/'price' fields."""
    try:
        data = await request.json()
        logging.info(f"📩 Incoming Chartink webhook: {json.dumps(data)}")

        # Chartink typically sends 'stocks' and 'trigger_prices' as comma-separated strings.
        if isinstance(data, dict) and ("stocks" in data or "trigger_prices" in data):
            stocks = data.get("stocks", "")
            prices = data.get("trigger_prices", "")
            stock_list = [s.strip() for s in stocks.split(",") if s.strip()]
            price_list = [float(p.strip()) for p in prices.split(",") if p.strip()]
            for i, symbol in enumerate(stock_list):
                price = price_list[i] if i < len(price_list) else 0
                if price > 0:
                    logging.info(f"✅ Trigger received → {symbol} @ {price}")
                    threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()
                else:
                    logging.warning(f"⚠️ Missing price for {symbol} in Chartink payload")
            return {"status": "ok"}

        # fallback: handle list of {symbol, price} or single {symbol, price}
        if isinstance(data, list):
            for item in data:
                symbol = item.get("symbol") or item.get("stocks")
                price = float(item.get("price") or item.get("trigger_prices") or 0)
                if symbol and price > 0:
                    logging.info(f"✅ Trigger received → {symbol} @ {price}")
                    threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()
            return {"status": "ok"}

        # single-object fallback
        symbol = data.get("symbol") or data.get("stocks") or ""
        price = float(data.get("price") or data.get("trigger_prices") or 0)
        if symbol and price > 0:
            logging.info(f"✅ Trigger received → {symbol} @ {price}")
            threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()
            return {"status": "ok"}

        logging.warning(f"⚠️ Unrecognized payload: {data}")
        return {"status": "ignored", "payload": data}

    except Exception as e:
        logging.error(f"❌ Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


# ---------------- STARTUP & SHUTDOWN ----------------
@app.on_event("startup")
def startup_event():
    global open_positions, fyers
    logging.info(f"🚀 Starting Chartink Webhook Service... (Mode: {TRADE_MODE})")

    # Init in-memory positions
    open_positions = {}

    # Initialize Fyers if token present (TEST mode still benefits from LTP queries)
    try:
        if FYERS_ACCESS_TOKEN:
            init_fyers()
            logging.info("✅ Fyers session initialized.")
        else:
            logging.warning("⚠️ FYERS_ACCESS_TOKEN not set — LTP/ohlc calls will fail until provided.")
    except Exception as e:
        logging.error(f"Fyers init error: {e}", exc_info=True)

    # Start the background monitor thread
    threading.Thread(target=monitor_exits, daemon=True).start()
    logging.info("🧠 Exit monitor started (ATR SL/TGT tracking active).")

    # Heartbeat (optional) to keep logs visible
    def heartbeat():
        while True:
            logging.info("💓 Heartbeat: app alive")
            time.sleep(300)
    threading.Thread(target=heartbeat, daemon=True).start()


@app.on_event("shutdown")
def shutdown_event():
    logging.info("🛑 Shutting down Chartink Webhook Service... saving summary to logs.")
    try:
        with lock:
            snapshot = dict(open_positions)
        if snapshot:
            logging.info(f"Open positions at shutdown: {json.dumps(snapshot)}")
    except Exception:
        logging.debug("Error producing shutdown snapshot", exc_info=True)


# ---------------- FYERS INIT FUNCTION ----------------
def init_fyers():
    global fyers
    fyers = fyersModel.FyersModel(client_id=FYERS_ID, token=FYERS_ACCESS_TOKEN, log_path=None)


# ---------------- RUN ----------------
if __name__ == "__main__":
    # If running as script, start normally (useful for local dev)
    try:
        # init fyers if token available
        if FYERS_ACCESS_TOKEN:
            init_fyers()
        # start background monitor
        threading.Thread(target=monitor_exits, daemon=True).start()
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)
    except Exception as e:
        logging.error(f"Failed to run app: {e}", exc_info=True)
