# chartink_webhook.py
from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime
# from breeze_connect import BreezeConnect  # Uncomment later for live trading

app = Flask(__name__)

# Connect to PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Create table if not exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS open_trades (
    id SERIAL PRIMARY KEY,
    symbol TEXT,
    buy_price FLOAT,
    qty INT,
    buy_time TIMESTAMP
);
""")
conn.commit()

@app.route('/chartink', methods=['POST'])
def chartink_alert():
    data = request.get_json(force=True)
    print(f"[{datetime.now()}] Received alert: {data}")

    for item in data:
        symbol = item.get('symbol')

        # Simulate getting LTP — replace later with Breeze API call
        ltp = 100.0  # Placeholder

        qty = 10  # example lot size
        buy_time = datetime.now()

        # Insert trade into DB
        cursor.execute(
            "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
            (symbol, ltp, qty, buy_time)
        )
        conn.commit()

        print(f"✅ Trade recorded in DB: {symbol} @ {ltp}")

        # --- Order placement logic (commented for now) ---
        # breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
        # breeze.generate_session(api_secret=os.getenv("BREEZE_API_SECRET"), session_token=os.getenv("BREEZE_SESSION_TOKEN"))
        # order_resp = breeze.place_order(stock_code=symbol, exchange_code="NSE", action="BUY", order_type="MARKET", quantity=qty)
        # print(f"Order placed: {order_resp}")
        # ------------------------------------------------

    return jsonify({"status": "success"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
