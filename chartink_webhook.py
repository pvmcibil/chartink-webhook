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

# Ensure Render’s connection string works even if SSL mode missing
urlparse.uses_netloc.append("postgres")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"


def get_db_connection():
    """Create a new database connection each time (avoids SSL idle errors)."""
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


# =====================================================
# Webhook Endpoint
# =====================================================
@app.route('/chartink', methods=['POST'])
def chartink_alert():
    data = request.get_json(force=True)
    print(f"[{datetime.now()}] Received alert: {data}")

    # Open DB connection safely inside the request
    conn = get_db_connection()
    cursor = conn.cursor()

    # Ensure table exists
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

    for item in data:
        symbol = item.get('symbol')
        if not symbol:
            continue

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

    # Close DB connection properly
    cursor.close()
    conn.close()

    return jsonify({"status": "success"}), 200


@app.route('/')
def home():
    return "Chartink Webhook Active ✅"


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
