# chartink_webhook.py
from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime
import urllib.parse as urlparse
# from breeze_connect import BreezeConnect  # Uncomment later for live trading

app = Flask(__name__)

# =====================================================
# Database Connection (Render-safe with SSL)
# =====================================================
DATABASE_URL = os.getenv("DATABASE_URL")

urlparse.uses_netloc.append("postgres")

# Render uses postgres:// which psycopg2 doesn’t like — fix it
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Add SSL mode if missing
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# Connect to PostgreSQL safely
conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
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

# =====================================================
# Webhook Endpoint — receives alerts from Chartink
# =====================================================
@app.route('/chartink', methods=['POST'])
def chartink_alert():
    data = request.get_json(force=True)
    print(f"[{datetime.now()}] Received alert: {data}")

    for item in data:
        symbol = item.get('symbol')
        if not symbol:
            continue

        # Simulate getting LTP — replace with Breeze API later
        ltp = 100.0  # Placeholder

        qty = 10  # Example quantity
        buy_time = datetime.now()

        # Insert into DB
        cursor.execute(
            "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
            (symbol, ltp, qty, buy_time)
        )
        conn.commit()

        print(f"✅ Trade recorded in DB: {symbol} @ {ltp}")

        # --- Order placement logic (commented for now) ---
        # breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
        # breeze.generate_session(api_secret=os.getenv("BREEZE_API_SECRET"),
        #                         session_token=os.getenv("BREEZE_SESSION_TOKEN"))
        # order_resp = breeze.place_order(
        #     stock_code=symbol,
        #     exchange_code="NSE",
        #     action="BUY",
        #     order_type="MARKET",
        #     quantity=qty
        # )
        # print(f"Order placed: {order_resp}")
        # ------------------------------------------------

    return jsonify({"status": "success"}), 200


# =====================================================
# Home endpoint for Render health check
# =====================================================
@app.route('/')
def home():
    return "✅ Chartink Webhook Active and Connected to DB"


# =====================================================
# Flask App Runner
# =====================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
