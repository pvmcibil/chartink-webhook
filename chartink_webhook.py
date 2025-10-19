from flask import Flask, request, jsonify
import os
import psycopg2
from datetime import datetime
import urllib.parse as urlparse
import time

# from breeze_connect import BreezeConnect  # Uncomment later for live trading

app = Flask(__name__)

# =====================================================
# Database Connection (with SSL + Retry)
# =====================================================
def get_db_connection():
    DATABASE_URL = os.getenv("DATABASE_URL")

    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL environment variable not set")

    urlparse.uses_netloc.append("postgres")
    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require"

    for attempt in range(3):
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            print(f"[{datetime.now()}] ✅ PostgreSQL connected")
            return conn
        except Exception as e:
            print(f"[{datetime.now()}] ⚠️ DB connection failed (attempt {attempt+1}/3): {e}")
            time.sleep(5)
    raise ConnectionError("❌ Unable to connect to PostgreSQL after 3 attempts")

# Establish connection
conn = get_db_connection()
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
# Webhook Endpoint
# =====================================================
@app.route('/chartink', methods=['POST'])
def chartink_alert():
    global conn, cursor

    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {e}"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    print(f"[{datetime.now()}] 📩 Received alert: {data}")

    for item in data:
        symbol = item.get('symbol')
        if not symbol:
            continue

        ltp = 100.0  # Placeholder for testing (replace with live LTP)
        qty = 10
        buy_time = datetime.now()

        try:
            # --- Order placement logic (commented for safe testing) ---
            """
            breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
            breeze.generate_session(
                api_secret=os.getenv("BREEZE_API_SECRET"),
                session_token=os.getenv("BREEZE_SESSION_TOKEN")
            )

            order_resp = breeze.place_order(
                stock_code=symbol,
                exchange_code="NSE",
                product="margin",
                action="BUY",
                order_type="MARKET",
                quantity=qty,
                validity="DAY"
            )

            if order_resp and "Success" in str(order_resp).lower():
                cursor.execute(
                    "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
                    (symbol, ltp, qty, buy_time)
                )
                conn.commit()
                print(f"[{datetime.now()}] ✅ Order success & recorded: {symbol} @ {ltp}")
            else:
                print(f"[{datetime.now()}] ⚠️ Order failed for {symbol}: {order_resp}")
            """
            # --- TEST MODE: Simulate successful order ---
            cursor.execute(
                "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
                (symbol, ltp, qty, buy_time)
            )
            conn.commit()
            print(f"[{datetime.now()}] 🧪 (TEST) Trade recorded in DB: {symbol} @ {ltp}")
            # -------------------------------------------------------------

        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            # Reconnect and retry once
            print(f"[{datetime.now()}] 🔄 Reconnecting DB...")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
                (symbol, ltp, qty, buy_time)
            )
            conn.commit()

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Error handling {symbol}: {e}")

    return jsonify({"status": "success"}), 200


@app.route('/')
def home():
    return "🚀 Chartink Webhook Active and Monitoring Orders"


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
