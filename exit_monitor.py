# exit_monitor.py
import threading
import time
import os
import json
import random
import traceback
from datetime import datetime
from flask import Flask, jsonify

# Optional: import BreezeConnect if you use Breeze live
# from breeze_connect import BreezeConnect
import psycopg2

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")  # set in Render env
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))  # default 60s
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() in ("1", "true", "yes")

def log(*args, **kwargs):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ", *args, **kwargs, flush=True)

def get_db_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)

def get_open_trades():
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, symbol, buy_price, qty FROM open_trades;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        trades = [{"id": r[0], "symbol": r[1], "buy_price": float(r[2]), "qty": int(r[3])} for r in rows]
        return trades
    except Exception as e:
        log("DB read error:", e)
        return []

def delete_trade(trade_id):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM open_trades WHERE id = %s;", (trade_id,))
        conn.commit()
        cur.close()
        conn.close()
        log(f"Deleted trade id={trade_id} from DB")
    except Exception as e:
        log("DB delete error:", e)

def get_ltp_sim(symbol):
    # Simulation fallback if no Breeze configured: random walk around 100
    return 100 * (1 + random.uniform(-0.01, 0.06))

def get_ltp_breeze(symbol):
    # implement Breeze quote call if you have BreezeConnect ready
    # Example (adjust per SDK): quote = breeze.get_quotes(stock_code=symbol, exchange_code="NSE")
    # return float(quote["Success"][0]["ltp"])
    raise NotImplementedError("Breeze LTP function not implemented")

def get_ltp(symbol):
    # Choose method: Breeze if env configured else simulation
    if os.getenv("USE_BREEZE", "false").lower() in ("1","true","yes"):
        try:
            return get_ltp_breeze(symbol)
        except Exception as e:
            log("Breeze LTP error:", e, "falling back to sim")
            return get_ltp_sim(symbol)
    else:
        return get_ltp_sim(symbol)

def place_sell(trade):
    symbol = trade["symbol"]
    qty = trade.get("qty", 1)
    if LIVE_MODE and os.getenv("USE_BREEZE", "false").lower() in ("1","true","yes"):
        try:
            # Implement your Breeze place order call here (uncomment and adapt)
            # resp = breeze.place_order(stock_code=symbol, exchange_code="NSE",
            #                           product="margin", action="sell",
            #                           order_type="market", quantity=str(qty),
            #                           price="0", validity="day")
            # log("Placed LIVE SELL:", symbol, resp)
            log("LIVE sell would be placed for", symbol, "— implement Breeze call")
            return True
        except Exception as e:
            log("Error placing live sell for", symbol, ":", e)
            return False
    else:
        log("Simulated SELL for", symbol, "qty=", qty)
        return True

def monitor_loop():
    log("Exit monitor thread started; checking every", CHECK_INTERVAL, "seconds.")
    while True:
        try:
            trades = get_open_trades()
            if not trades:
                log("No open trades.")
            for t in trades:
                trade_id = t["id"]
                symbol = t["symbol"]
                buy_price = float(t["buy_price"])
                ltp = get_ltp(symbol)
                if ltp is None:
                    log("LTP unavailable for", symbol)
                    continue
                change = ((ltp - buy_price) / buy_price) * 100
                log(f"Check {symbol}: buy={buy_price:.2f} ltp={ltp:.2f} change={change:.2f}%")
                if change <= -0.5 or change >= 4.0:
                    log(f"Exit condition met for {symbol}: change={change:.2f}% -> placing SELL")
                    ok = place_sell(t)
                    if ok:
                        delete_trade(trade_id)
            # end for
        except Exception as e:
            log("Monitor loop error:", e)
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL)

# Health route so Render can check service
@app.route("/")
def health():
    return jsonify({"status":"ok", "monitor":"running"})

if __name__ == "__main__":
    # start monitor thread
    th = threading.Thread(target=monitor_loop, daemon=True)
    th.start()
    # run Flask so Render sees a port
    port = int(os.getenv("PORT", "10000"))
    log("Starting Flask on port", port)
    app.run(host="0.0.0.0", port=port)
