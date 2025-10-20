from flask import Flask, request, jsonify
import os
import psycopg2
import pandas as pd
from datetime import datetime
import time
from breeze_connect import BreezeConnect  # ✅ Live Breeze API

app = Flask(__name__)

# =====================================================
# Database Connection (with SSL + Retry)
# =====================================================
def get_db_connection():
    """Robust PostgreSQL connection with retry mechanism."""
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
    stock_code TEXT,
    trigger_price FLOAT,
    buy_price FLOAT,
    qty INT,
    buy_time TIMESTAMP
);
""")
conn.commit()

# =====================================================
# Breeze Connection + Instrument Cache
# =====================================================
def get_breeze():
    """Authenticate Breeze and return connection"""
    api_key = os.getenv("BREEZE_API_KEY")
    api_secret = os.getenv("BREEZE_API_SECRET")
    session_token = os.getenv("BREEZE_SESSION_TOKEN")

    if not all([api_key, api_secret, session_token]):
        raise ValueError("❌ Missing Breeze credentials (check env variables).")

    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        print(f"[{datetime.now()}] ✅ Breeze API authenticated successfully")
        return breeze
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Breeze authentication failed: {e}")
        return None


# =====================================================
# Load / Cache Instrument List
# =====================================================
def load_instruments(breeze):
    """Fetch or load cached Breeze instruments"""
    try:
        if os.path.exists("breeze_instruments.csv"):
            df = pd.read_csv("breeze_instruments.csv")
            print(f"[{datetime.now()}] ⚡ Loaded cached instruments ({len(df)} rows)")
        else:
            print(f"[{datetime.now()}] ⏳ Fetching instruments from Breeze...")
            instruments = breeze.get_instruments()
            df = pd.DataFrame(instruments)
            df.to_csv("breeze_instruments.csv", index=False)
            print(f"[{datetime.now()}] ✅ Cached {len(df)} instruments to file")
        return df
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Error loading instruments: {e}")
        return pd.DataFrame()


def get_stock_code(symbol):
    """Lookup ICICI-specific stock_code for NSE symbol"""
    match = instrument_df[
        (instrument_df['exchange_code'] == 'NSE') &
        (instrument_df['symbol'].str.upper() == symbol.upper())
    ]
    if not match.empty:
        return match.iloc[0]['stock_code']
    return None


# =====================================================
# Initialize Breeze + Instrument Cache
# =====================================================
breeze = get_breeze()
instrument_df = load_instruments(breeze)

# =====================================================
# Webhook Endpoint — Chartink → Breeze → PostgreSQL
# =====================================================
@app.route('/chartink', methods=['POST'])
def chartink_alert():
    global conn, cursor, breeze, instrument_df

    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {e}"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    print(f"[{datetime.now()}] 📩 Received alert: {data}")

    # Reconnect Breeze if expired
    if not breeze:
        breeze = get_breeze()
        instrument_df = load_instruments(breeze)

    # ✅ Parse stocks and trigger prices
    stocks_str = data.get('stocks', '')
    prices_str = data.get('trigger_prices', '')
    stock_list = [s.strip() for s in stocks_str.split(',') if s.strip()]
    price_list = [p.strip() for p in prices_str.split(',') if p.strip()]
    stock_pairs = list(zip(stock_list, price_list + ['0'] * (len(stock_list) - len(price_list))))

    for symbol, trigger_price in stock_pairs:
        qty = 10
        buy_time = datetime.now()

        try:
            print(f"[{datetime.now()}] 🟢 Processing {symbol} @ Trigger {trigger_price}")

            stock_code = get_stock_code(symbol)
            if not stock_code:
                print(f"[{datetime.now()}] ⚠️ Skipping {symbol} — no Breeze stock_code found.")
                continue

            # ✅ Place Live Order (BUY)
            order_resp = breeze.place_order(
                stock_code=stock_code,
                exchange_code="NSE",
                product="margin",
                action="BUY",
                order_type="MARKET",
                quantity=qty,
                validity="DAY"
            )

            if order_resp and "Success" in str(order_resp).lower():
                try:
                    # Fetch live price
                    quote = breeze.get_quotes(
                        stock_code=stock_code,
                        exchange_code="NSE",
                        product_type="cash"
                    )
                    ltp = float(quote.get("Success", [{}])[0].get("ltp", 0))
                except Exception:
                    ltp = 0.0

                # Insert into database
                cursor.execute(
                    "INSERT INTO open_trades (symbol, stock_code, trigger_price, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s, %s, %s)",
                    (symbol, stock_code, float(trigger_price), ltp, qty, buy_time)
                )
                conn.commit()
                print(f"[{datetime.now()}] ✅ Order success & recorded: {symbol} ({stock_code}) @ {ltp}")

            else:
                print(f"[{datetime.now()}] ⚠️ Order failed for {symbol}: {order_resp}")

        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            print(f"[{datetime.now()}] 🔄 Lost DB connection, retrying...")
            conn = get_db_connection()
            cursor = conn.cursor()

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Error handling {symbol}: {e}")

    return jsonify({"status": "success"}), 200


# =====================================================
# Health Check Endpoint
# =====================================================
@app.route('/')
def home():
    return "🚀 Chartink Webhook Live — Breeze + DB Connected + Stock Code Mapping"


# =====================================================
# Run Flask App
# =====================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
