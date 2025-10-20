# =====================================================
# exit_monitor_live_test.py  ✅ (LIVE TEST MODE)
# =====================================================
import os
import time
import psycopg2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from breeze_connect import BreezeConnect   # ✅ Use Breeze for live connection


# =====================================================
# Database Setup with SSL + Retry
# =====================================================
def get_db_connection():
    """Reconnect-safe PostgreSQL connection with retries"""
    DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://stock_list_hfv1_user:X7S7HahRta8wISA5vz7GtAnmMC3aHX5g@dpg-d3qdhlk9c44c73cm5iu0-a.singapore-postgres.render.com/stock_list_hfv1"
    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require"

    for attempt in range(3):
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            conn.autocommit = True
            print(f"[{datetime.now()}] ✅ PostgreSQL connected (attempt {attempt+1})")
            return conn
        except Exception as e:
            print(f"[{datetime.now()}] ⚠️ DB connection failed ({attempt+1}/3): {e}")
            time.sleep(5)
    raise ConnectionError("❌ Unable to connect to PostgreSQL after 3 attempts")


# Initial connection
conn = get_db_connection()
cursor = conn.cursor()


# =====================================================
# Breeze Setup
# =====================================================
print(f"[{datetime.now()}] 🔄 Connecting to Breeze...")

BREEZE_API_KEY = os.getenv("BREEZE_API_KEY")
BREEZE_API_SECRET = os.getenv("BREEZE_API_SECRET")
BREEZE_SESSION_TOKEN = os.getenv("BREEZE_SESSION_TOKEN")

if not (BREEZE_API_KEY and BREEZE_API_SECRET and BREEZE_SESSION_TOKEN):
    raise ValueError("❌ Missing Breeze API credentials in environment variables!")

breeze = BreezeConnect(api_key=BREEZE_API_KEY)
breeze.generate_session(api_secret=BREEZE_API_SECRET, session_token=BREEZE_SESSION_TOKEN)
print(f"[{datetime.now()}] ✅ Breeze connection established successfully!")


# =====================================================
# Utility: Fetch LTP safely
# =====================================================
def get_ltp(symbol):
    """Fetch live LTP from Breeze"""
    try:
        quote = breeze.get_quotes(stock_code=symbol, exchange_code="NSE", expiry_date=None)
        if quote and 'Success' in quote.get('Status', ''):
            ltp = float(quote['Success'][0]['ltp'])
            return ltp
        else:
            print(f"[{datetime.now()}] ⚠️ No LTP data for {symbol} → {quote}")
            return None
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Error fetching LTP for {symbol}: {e}")
        return None


# =====================================================
# Exit Check (NO ORDER PLACEMENT)
# =====================================================
def check_exit_condition(trade):
    """Check exit condition and log LTP (no sell order executed)"""
    trade_id, symbol, buy_price, qty = trade
    ltp = get_ltp(symbol)
    if not ltp:
        return None

    change_pct = ((ltp - buy_price) / buy_price) * 100
    print(f"[{datetime.now()}] 🔍 {symbol} | LTP={ltp} | Δ={change_pct:.2f}%")

    # Only simulate exit (no order)
    if change_pct <= -0.5 or change_pct >= 4:
        print(f"[{datetime.now()}] 🚨 Exit condition met for {symbol}, but order skipped (TEST MODE)")


# =====================================================
# Main Loop (Performance Tracked)
# =====================================================
def monitor_loop():
    global conn, cursor
    print(f"🚀 Exit monitor (LIVE LTP CHECK MODE) started at {datetime.now()}")

    while True:
        try:
            cursor.execute("SELECT id, symbol, buy_price, qty FROM open_trades")
            trades = cursor.fetchall()

            if not trades:
                print(f"[{datetime.now()}] 💤 No open trades to monitor.")
                time.sleep(60)
                continue

            print(f"[{datetime.now()}] 🔍 Checking {len(trades)} open trades...")

            start_time = time.time()

            # Parallel checks
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(check_exit_condition, trade) for trade in trades]
                for future in as_completed(futures):
                    future.result()

            duration = round(time.time() - start_time, 2)
            print(f"✅ Checked {len(trades)} stocks in {duration} seconds.\n")

            # Save to performance log
            with open("performance_log.txt", "a") as f:
                f.write(f"{datetime.now()} | {len(trades)} trades | {duration}s\n")

            time.sleep(60)

        except psycopg2.Error:
            print(f"[{datetime.now()}] ⚠️ DB connection lost. Retrying...")
            time.sleep(5)
            conn = get_db_connection()
            cursor = conn.cursor()
        except Exception as e:
            print(f"[{datetime.now()}] ❌ Unexpected error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    monitor_loop()
