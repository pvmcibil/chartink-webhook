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
    """Robust PostgreSQL connection with infinite retry"""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL environment variable not set")

    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require"

    attempt = 0
    while True:
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            conn.autocommit = True
            print(f"[{datetime.now()}] ✅ PostgreSQL connected successfully")
            return conn
        except Exception as e:
            attempt += 1
            print(f"[{datetime.now()}] ⚠️ Retry {attempt}: Unable to connect to PostgreSQL ({e})")
            time.sleep(10)


# Establish initial DB connection
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

        ltp = 100.0  # Placeholder for now (use Breeze LTP later)
        qty = 10
        buy_time = datetime.now()

        try:
            # --- Order placement logic (commented for now) ---
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
            # --- TEST MODE: Simulate successful order insertion ---
            cursor.execute(
                "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
                (symbol, ltp, qty, buy_time)
            )
            conn.commit()
            print(f"[{datetime.now()}] 🧪 (TEST) Trade recorded in DB: {symbol} @ {ltp}")
            # -------------------------------------------------------

        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            print(f"[{datetime.now()}] 🔄 Lost DB connection, retrying...")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
                (symbol, ltp, qty, buy_time)
            )
            conn.commit()
            print(f"[{datetime.now()}] ✅ Reconnected and recorded: {symbol}")

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Error handling {symbol}: {e}")

    return jsonify({"status": "success"}), 200


# =====================================================
# Health Check Endpoint
# =====================================================
@app.route('/')
def home():
    return "🚀 Chartink Webhook Active — DB Connected and Ready"


# =====================================================
# Run Flask App
# =====================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
