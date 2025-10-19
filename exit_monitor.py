# exit_monitor.py
import os
import time
import psycopg2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from breeze_connect import BreezeConnect

# =====================================================
# Database Setup
# =====================================================
def get_db_connection():
    """Reconnect-safe PostgreSQL connection"""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if DATABASE_URL and "sslmode" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require"
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    return conn

conn = get_db_connection()
cursor = conn.cursor()

# =====================================================
# Breeze Setup
# =====================================================
breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
breeze.generate_session(
    api_secret=os.getenv("BREEZE_API_SECRET"),
    session_token=os.getenv("BREEZE_SESSION_TOKEN")
)

# =====================================================
# Utility: Fetch LTP safely
# =====================================================
def get_ltp(symbol):
    """Fetch live LTP for a given symbol using Breeze API"""
    try:
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
# Utility: Sell logic
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
            print(f"[{datetime.now()}] 🚨 Exit condition met for {symbol}: {change_pct:.2f}%")
            # --- Place sell order (commented for test) ---
            # resp = breeze.place_order(
            #     stock_code=symbol,
            #     exchange_code="NSE",
            #     action="SELL",
            #     order_type="MARKET",
            #     quantity=qty
            # )
            # print(f"✅ Sell order placed for {symbol}: {resp}")
            # ------------------------------------------------

            # Delete only if sell success
            cursor.execute("DELETE FROM open_trades WHERE id = %s", (trade_id,))
            print(f"🧹 Record deleted for {symbol} (Trade ID: {trade_id})")

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Error selling {symbol}: {e}")
    else:
        print(f"[{datetime.now()}] ⏳ {symbol} | LTP={ltp} | Δ={change_pct:.2f}%")

# =====================================================
# Main Loop
# =====================================================
def monitor_loop():
    print(f"🚀 Exit monitor started at {datetime.now()}")
    while True:
        try:
            cursor.execute("SELECT id, symbol, buy_price, qty FROM open_trades")
            trades = cursor.fetchall()

            if not trades:
                print(f"[{datetime.now()}] 💤 No open trades to monitor.")
                time.sleep(60)
                continue

            print(f"[{datetime.now()}] 🔍 Checking {len(trades)} open trades...")

            # Thread pool for parallel LTP fetch + logic
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(check_and_sell, trade) for trade in trades]
                for future in as_completed(futures):
                    future.result()

            time.sleep(60)

        except psycopg2.Error:
            print(f"[{datetime.now()}] ⚠️ Database connection lost, reconnecting...")
            time.sleep(5)
            global conn, cursor
            conn = get_db_connection()
            cursor = conn.cursor()

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Unexpected error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    monitor_loop()
