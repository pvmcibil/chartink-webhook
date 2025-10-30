#!/usr/bin/env python3
"""
Render-safe trading bot with ATR-based SL/Target, TEST/REAL mode,
in-memory positions, and daily Excel + email summary.

Author: venu madhav (2025)
"""

import os
import time
import json
import threading
import logging
import datetime as dt
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
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------------- GLOBALS ----------------
app = FastAPI()
fyers = None
open_positions = {}  # In-memory positions
lock = threading.Lock()


# ---------------- HELPERS ----------------
def now_ist():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)


def get_atr(df, period=14):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
    df["ATR"] = df["TR"].rolling(period).mean()
    return df["ATR"].iloc[-1]


def fetch_ohlc(symbol, interval="5", limit=100):
    try:
        data = fyers.history({
            "symbol": symbol,
            "resolution": interval,
            "date_format": "1",
            "range_from": (now_ist() - dt.timedelta(days=10)).strftime("%Y-%m-%d"),
            "range_to": now_ist().strftime("%Y-%m-%d"),
            "cont_flag": "1"
        })
        if "candles" not in data:
            return None
        df = pd.DataFrame(data["candles"], columns=["ts", "open", "high", "low", "close", "vol"])
        return df
    except Exception as e:
        logging.warning(f"fetch_ohlc failed for {symbol}: {e}")
        return None


def get_ltp(symbol):
    try:
        q = fyers.quotes({"symbols": symbol})
        return q["d"][0]["v"]["lp"]
    except Exception as e:
        logging.warning(f"LTP error {symbol}: {e}")
        return None


# ---------------- TRADING CORE ----------------
def calculate_sl_tgt(entry, atr):
    if atr and STOP_METHOD == "ATR":
        sl = entry - (ATR_MULT * atr)
        tgt = entry + (ATR_TARGET_MULT * atr)
    else:
        sl = entry * (1 - SL_PCT / 100)
        tgt = entry * (1 + TARGET_PCT / 100)
    return round(sl, 2), round(tgt, 2)


def place_order(symbol, price, qty, side):
    if TRADE_MODE == "REAL":
        try:
            resp = fyers.place_order({
                "symbol": symbol,
                "qty": qty,
                "type": 2,
                "side": 1 if side == "BUY" else -1,
                "productType": "INTRADAY",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY"
            })
            logging.info(f"REAL {side} placed for {symbol}: {resp}")
        except Exception as e:
            logging.error(f"Order failed {symbol}: {e}")
    else:
        logging.info(f"TEST {side} simulated for {symbol} @ {price}")


def secure_place_thread(symbol, price):
    with lock:
        if symbol in open_positions:
            logging.info(f"Duplicate {symbol} ignored.")
            return
        df = fetch_ohlc(symbol)
        atr = get_atr(df, ATR_PERIOD) if df is not None else None
        sl, tgt = calculate_sl_tgt(price, atr)
        qty = 1
        open_positions[symbol] = {
            "entry_price": price,
            "atr": atr,
            "stop_loss": sl,
            "target": tgt,
            "qty": qty,
            "timestamp": now_ist().isoformat(),
            "status": f"{TRADE_MODE}_OPEN",
        }
        place_order(symbol, price, qty, "BUY")
        logging.info(f"{symbol} opened @ {price}, SL {sl}, TGT {tgt}")


# ---------------- EXIT MONITOR ----------------
def monitor_exits():
    while True:
        try:
            with lock:
                for symbol, pos in list(open_positions.items()):
                    if pos["status"].endswith("EXIT_SL") or pos["status"].endswith("EXIT_TGT"):
                        continue
                    ltp = get_ltp(symbol)
                    if not ltp:
                        continue
                    entry, sl, tgt = pos["entry_price"], pos["stop_loss"], pos["target"]
                    if ltp <= sl:
                        place_order(symbol, ltp, pos["qty"], "SELL")
                        pos.update({
                            "exit_price": ltp,
                            "exit_reason": "SL_HIT",
                            "status": f"{TRADE_MODE}_EXIT_SL"
                        })
                        logging.info(f"{symbol} SL hit @ {ltp}")
                    elif ltp >= tgt:
                        place_order(symbol, ltp, pos["qty"], "SELL")
                        pos.update({
                            "exit_price": ltp,
                            "exit_reason": "TGT_HIT",
                            "status": f"{TRADE_MODE}_EXIT_TGT"
                        })
                        logging.info(f"{symbol} TGT hit @ {ltp}")
        except Exception as e:
            logging.warning(f"monitor_exits: {e}")
        time.sleep(30)


# ---------------- EMAIL SUMMARY ----------------
import tempfile
def email_summary():
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        logging.warning("Email credentials not configured.")
        return
    try:
        df = pd.DataFrame(open_positions).T
        if df.empty:
            logging.info("No trades today.")
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
        logging.error(f"Email send failed: {e}")


# ---------------- API ENDPOINT ----------------
@app.post("/chartink")
async def webhook(request: Request):
    data = await request.json()
    if isinstance(data, list):
        for item in data:
            symbol = item.get("symbol")
            price = float(item.get("price", 0))
            if symbol and price > 0:
                threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()
    else:
        symbol = data.get("symbol")
        price = float(data.get("price", 0))
        if symbol and price > 0:
            threading.Thread(target=secure_place_thread, args=(symbol, price), daemon=True).start()
    return {"status": "ok"}


# ---------------- STARTUP ----------------
def init_fyers():
    global fyers
    fyers = fyersModel.FyersModel(client_id=FYERS_ID, token=FYERS_ACCESS_TOKEN, log_path=None)
    logging.info("Fyers session initialized.")


def start_exit_thread():
    threading.Thread(target=monitor_exits, daemon=True).start()
    logging.info("Exit monitor started.")


if __name__ == "__main__":
    init_fyers()
    start_exit_thread()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
