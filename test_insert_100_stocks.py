import psycopg2
import os
from datetime import datetime
import random
import urllib.parse as urlparse

# ============================================================
#  Database Connection (Render-safe with Fallback)
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL")

# If not set as env var, fallback to hardcoded Render DB URL
if not DATABASE_URL:
    DATABASE_URL = "postgresql://stock_list_hfv1_user:X7S7HahRta8wISA5vz7GtAnmMC3aHX5g@dpg-d3qdhlk9c44c73cm5iu0-a.singapore-postgres.render.com/stock_list_hfv1"

# Ensure SSL mode
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# Parse connection URL
urlparse.uses_netloc.append("postgres")

# Try connecting to database
try:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    cursor = conn.cursor()
    print("✅ Connected to Render PostgreSQL successfully!")
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    exit(1)

# ============================================================
# Ensure table exists
# ============================================================
cursor.execute("""
CREATE TABLE IF NOT EXISTS open_trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20),
    buy_price FLOAT,
    qty INT,
    buy_time TIMESTAMP
)
""")
print("🧱 Verified table: open_trades")

# ============================================================
# Insert 100 mock records
# ============================================================
symbols = [
    "RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "ITC", "LT",
    "BHARTIARTL", "MARUTI", "SUNPHARMA", "HINDUNILVR", "ASIANPAINT", "TITAN", "BAJFINANCE", "ULTRACEMCO", "NTPC", "POWERGRID",
    "ONGC", "COALINDIA", "TATAMOTORS", "M&M", "HCLTECH", "WIPRO", "NESTLEIND", "TECHM", "TATASTEEL", "ADANIENT",
    "ADANIPORTS", "GRASIM", "HDFCLIFE", "SBILIFE", "BRITANNIA", "BAJAJFINSV", "CIPLA", "EICHERMOT", "HEROMOTOCO", "DIVISLAB",
    "DRREDDY", "UPL", "JSWSTEEL", "BPCL", "HINDALCO", "TATACONSUM", "APOLLOHOSP", "BAJAJ-AUTO", "ICICIPRULI", "INDUSINDBK",
    "DLF", "PIDILITIND", "SHREECEM", "BEL", "SIEMENS", "IRCTC", "BANKBARODA", "TRENT", "TVSMOTOR", "PNB",
    "INDIGO", "DMART", "ZOMATO", "PAYTM", "TATAPOWER", "ABB", "TATACHEM", "HAL", "GAIL", "COLPAL",
    "AMBUJACEM", "HAVELLS", "VEDL", "TORNTPHARM", "ADANIGREEN", "ADANITRANS", "BANDHANBNK", "LTIM", "LICI", "POLYCAB",
    "BOSCHLTD", "GLAND", "DALBHARAT", "MOTHERSON", "LODHA", "BERGEPAINT", "BHEL", "GODREJCP", "UBL", "YESBANK",
    "IDFCFIRSTB", "RECLTD", "ICICIGI", "ABBOTINDIA", "CONCOR", "OFSS", "SRF", "MCDOWELL-N", "PAGEIND", "IRFC"
]

random.shuffle(symbols)
test_records = symbols[:100]

# Insert 100 records
for symbol in test_records:
    buy_price = round(random.uniform(100, 1000), 2)
    qty = random.choice([5, 10, 15, 20])
    buy_time = datetime.now()

    cursor.execute(
        "INSERT INTO open_trades (symbol, buy_price, qty, buy_time) VALUES (%s, %s, %s, %s)",
        (symbol, buy_price, qty, buy_time)
    )

print("✅ Inserted 100 mock trade records into open_trades table successfully!")

# Close connection
cursor.close()
conn.close()
