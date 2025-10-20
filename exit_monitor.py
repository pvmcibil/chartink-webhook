# =====================================================
# exit_monitor_live.py — Live Breeze Exit Monitor
# =====================================================
import os
import time
import psycopg2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from breeze_connect import BreezeConnect  # ✅ Live API

# =====================================================
# Database Connection
# =====================================================
def get_db_connection():
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL environment variable not set")

    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require"

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    print(f"[{datetime.now()}] ✅ Connected to PostgreSQL")
    return conn


conn = get_db_connection()
cursor = conn.cursor()

# =====================================================
# BreezeConnect Setup
# =====================================================
try:
    breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
    breeze.generate_session(
        api_secret=os.getenv("BREEZE_API_SECRET"),
        session_token=os.getenv("BREEZE_SESSION_TOKEN")  # ✅ fixed variable name
    )
    print(f"[{datetime.now()}] 🌐 BreezeConnect session established successfully!")
except Exception as e:
    print(f"[{datetime.now()}] ❌ BreezeConnect login failed: {e}")
    raise SystemExit(1)

# =====================================================
# Utility — Fetch LTP for a stock
# =====================================================
def get_ltp(symbol):
    """Fetch live LTP for a given NSE stock symbol."""
    try:
        quote = breeze.get_quotes(
            stock_code=symbol,
            exchange_code="NSE",
            product_type="cash",
            right="others",
            expiry_date=None
        )

        if isinstance(quote, dict) and "Success" in quote:
            ltp = float(quote["Success"][0]["ltp"])
            print(f"[{datetime.now()}] ✅ {symbol} | LTP = {ltp}")
            return ltp
        else:
            print(f"[{datetime.now()}] ⚠️ Invalid response for {symbol}: {quote}")
            return None

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Breeze LTP fetch failed for {symbol}: {e}")
        return None


# =====================================================
# Sell Logic — LIVE ORDERS
# =====================================================
def check_and_sell(trade):
    """Check exit conditions and place live SELL order via Breeze."""
    trade_id, symbol, buy_price, qty = trade
    ltp = get_ltp(symbol)
    if not ltp:
        return None

    change_pct = ((ltp - buy_price) / buy_price) * 100

    if change_pct <= -0.5 or change_pct >= 4:
        print(f"[{datetime.now()}] 🚨 Exit signal for {symbol}: Δ={change_pct:.2f}%")

        try:
            resp = breeze.place_order(
                stock_code=symbol,
                exchange_code="NSE",
                product="margin",
                action="SELL",
                order_type="MARKET",
                quantity=qty,
                validity="DAY"
            )

            print(f"[{datetime.now()}] ✅ SELL ORDER placed for {symbol} | Response: {resp}")

            # Record sell in DB
            cursor.execute(
                "DELETE FROM open_trades WHERE id = %s",
                (trade_id,)
            )
            conn.commit()
            print(f"[{datetime.now()}] 🗑️ Removed {symbol} from open_trades after SELL")

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Order placement failed for {symbol}: {e}")

    else:
        print(f"[{datetime.now()}] ⏳ Holding {symbol} | Δ={change_pct:.2f}%")


# =====================================================
# Monitor Loop (One-time run)
# =====================================================
def monitor_once():
    print(f"\n🚀 Starting live LTP check at {datetime.now()}")

    cursor.execute("SELECT id, symbol, buy_price, qty FROM open_trades LIMIT 100;")
    trades = cursor.fetchall()

    if not trades:
        print("No open trades found.")
        return

    print(f"🔍 Checking {len(trades)} stocks via Breeze...")

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(check_and_sell, trade) for trade in trades]
        for future in as_completed(futures):
            future.result()

    duration = round(time.time() - start_time, 2)
    print(f"\n✅ Completed LTP fetch for {len(trades)} stocks in {duration} seconds")

    # Save to local log
    with open("performance_log.txt", "a") as f:
        f.write(f"{datetime.now()} | {len(trades)} stocks | {duration}s\n")


if __name__ == "__main__":
    monitor_once()
