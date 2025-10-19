# exit_monitor.py
import time
import psycopg2
import os
from datetime import datetime
# from breeze_connect import BreezeConnect  # Uncomment later for real exit

DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

def get_ltp(symbol):
    """Simulate live price — replace with Breeze quote later"""
    import random
    # Randomly simulate small moves
    return 100 * (1 + random.uniform(-0.01, 0.05))

def check_and_exit_trades():
    cursor.execute("SELECT id, symbol, buy_price, qty FROM open_trades;")
    trades = cursor.fetchall()

    for trade in trades:
        trade_id, symbol, buy_price, qty = trade
        ltp = get_ltp(symbol)
        change = ((ltp - buy_price) / buy_price) * 100

        print(f"[{datetime.now()}] Checking {symbol}: LTP={ltp:.2f} | Change={change:.2f}%")

        # Exit if price down more than 0.5% or up more than 4%
        if change <= -0.5 or change >= 4.0:
            print(f"🚀 Exiting {symbol}: change {change:.2f}%")

            # --- Place SELL order (commented for now) ---
            # breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
            # breeze.generate_session(api_secret=os.getenv("BREEZE_API_SECRET"), session_token=os.getenv("BREEZE_SESSION_TOKEN"))
            # order_resp = breeze.place_order(stock_code=symbol, exchange_code="NSE", action="SELL", order_type="MARKET", quantity=qty)
            # print(f"Sell order placed: {order_resp}")
            # ------------------------------------------------

            # Remove from DB
            cursor.execute("DELETE FROM open_trades WHERE id = %s;", (trade_id,))
            conn.commit()
            print(f"✅ Deleted {symbol} from open_trades after exit.")

while True:
    try:
        check_and_exit_trades()
        time.sleep(60)  # check every 1 minute
    except Exception as e:
        print("⚠️ Error in monitor loop:", e)
        time.sleep(10)
