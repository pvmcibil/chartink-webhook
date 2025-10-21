from breeze_connect import BreezeConnect
import os

# --- Step 1: User Inputs ---
stock_symbol = input("INDIAGLYCO").upper()
# Append .EQ if not present
if not stock_symbol.endswith(".EQ"):
    stock_code = stock_symbol + ".EQ"
else:
    stock_code = stock_symbol

# --- Step 2: API Credentials ---
# Replace with your Breeze credentials
api_key = "1Z3719h9C9I582Jk8M3730779g72l912"
api_secret = "E46&955NK6739B4x03_6rT1O=UH10F12"
session_token = "53408636"  # You need to generate this via login flow

# --- Step 3: Initialize BreezeConnect ---
breeze = BreezeConnect(api_key=api_key)

# Set session token
breeze.generate_session(api_secret=api_secret, session_token=session_token)

# --- Step 4: Fetch LTP (Last Traded Price) ---
try:
    result = breeze.get_quotes(stock_code=stock_symbol,
                               exchange_code="NSE",
                               product_type="cash",
                               expiry_date=None,
                               right=None,
                               strike_price=None)

    ltp = result.get("last_traded_price", "N/A")
    print(f"\nCurrent price of {stock_symbol} is: ₹{ltp}")

except Exception as e:
    print(f"\nError: {str(e)}")
