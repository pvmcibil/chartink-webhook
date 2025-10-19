# =====================================================
# exit_monitor.py (Fixed & Final)
# =====================================================
import os
import time
import psycopg2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
# from breeze_connect import BreezeConnect  # Uncomment later for live trading


# =====================================================
# Database Setup with SSL + Retry
# =====================================================
def get_db_connection():
    """Reconnect-safe PostgreSQL connection with retries"""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL environment variable not set")

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
# Test Mode Config (to skip real order placement)
# =====================================================
TEST_MODE = True  # ⬅️ Set to False when live with Breeze


# =====================================================
# Breeze Setup (disabled in test mode)
# =====================================================
if not TEST_MODE:
    from breeze_connect import BreezeConnect
    breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
    breeze.generate_session(
        api_secret=os.getenv("BREEZE_API_SECRET"),
        session_token=os.getenv("BREEZE_SESSION_TOKEN")
    )


# =====================================================
# Utility: Fetch LTP safely
# =====================================================
def get_ltp(symbol):
    """Fetch live LTP for a given symbol (mocked in TEST_MODE)"""
    try:
        if TEST_MODE:
            # Simulate random market movement for testing
            import random
            return round(100 + random.uniform(-3, 6), 2)

        quote = breeze.get_quotes(stock_code=symbol, exchange_code="NSE", expiry_date=None)
        if quote and 'Success' in quote.get('Status', ''):
            ltp = float(quote['Success'][0]['ltp'])
            return ltp
        else:
            print(f"[{datetime.now()}] ⚠️ No LTP data for {symbol}")
            return None
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Error fetching LTP for {symbol}: {e}")
        return None


# =====================================================
# Sell Logic
# =====================================================
def check_and_sell(trade):
    """Check exit condition for one trade, and sell if target or stop hit"""
    trade_id, symbol, buy_price, qty = trade
    ltp = get_ltp(symbol)
    if not ltp:
        return None

    change_pct = ((ltp - buy_price) / buy_price) * 100

    if change_pct <= -0.5 or change_pct >= 4:
        try:
            print(f"[{datetime.now()}] 🚨 Exit condition met for {symbol}: Δ={change_pct:.2f}%")

            if not TEST_MODE:
                # --- Real Sell Order ---
                resp = breeze.place_order(
                    stock_code=symbol,
                    exchange_code="NSE",
                    action="SELL",
                    order_type="MARKET",
                    quantity=qty
                )
                if resp and "Success" in str(resp):
                    cursor.execute("DELETE FROM open_trades WHERE id = %s", (trade_id,))
                    print(f"✅ Sell success → Record deleted for {symbol} (Trade ID: {trade_id})")
                else:
                    print(f"⚠️ Sell failed for {symbol}: {resp}")
            else:
                # --- Test Mode: simulate successful sell ---
                cursor.execute("DELETE FROM open_trades WHERE id = %s", (trade_id,))
                print(f"🧪 (TEST) Record deleted for {symbol} (Trade ID: {trade_id})")

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Error selling {symbol}: {e}")
    else:
        print(f"[{datetime.now()}] ⏳ {symbol} | LTP={ltp} | Δ={change_pct:.2f}%")


# =====================================================
# Main Loop (with Performance Timing)
# =====================================================
def monitor_loop():
    global conn, cursor  # ✅ FIXED: declared before use
    print(f"🚀 Exit monitor started at {datetime.now()} (TEST_MODE={TEST_MODE})")

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

            # Thread pool for parallel work
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(check_and_sell, trade) for trade in trades]
                for future in as_completed(futures):
                    future.result()

            duration = round(time.time() - start_time, 2)
            print(f"✅ Batch processed: {len(trades)} trades in {duration} seconds")

            with open("performance_log.txt", "a") as f:
                f.write(f"{datetime.now()} | {len(trades)} trades | {duration}s\n")

            time.sleep(60)

        except psycopg2.Error:
            print(f"[{datetime.now()}] ⚠️ Database connection lost, reconnecting...")
            time.sleep(5)
            conn = get_db_connection()
            cursor = conn.cursor()

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Unexpected error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    monitor_loop()
