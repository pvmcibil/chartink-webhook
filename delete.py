# ======================================================
# clean_open_trades.py
# ======================================================
import psycopg2
import os
import urllib.parse as urlparse

# ======================================================
# Database Connection
# ======================================================
DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://stock_list_hfv1_user:X7S7HahRta8wISA5vz7GtAnmMC3aHX5g@dpg-d3qdhlk9c44c73cm5iu0-a.singapore-postgres.render.com/stock_list_hfv1"

# Ensure SSL
urlparse.uses_netloc.append("postgres")
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# Connect to DB
print("🔗 Connecting to PostgreSQL...")
conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
cursor = conn.cursor()
print("✅ Connected!")

# ======================================================
# Clean Table
# ======================================================
try:
    cursor.execute("DELETE FROM open_trades;")
    conn.commit()
    print("🧹 All rows deleted from 'open_trades' successfully!")
except Exception as e:
    print(f"❌ Error cleaning table: {e}")
finally:
    cursor.close()
    conn.close()
    print("🔒 Connection closed.")
