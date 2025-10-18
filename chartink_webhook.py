# chartink_webhook.py
from flask import Flask, request, jsonify
from breeze_connect import Breeze
import os
import json
from datetime import datetime

app = Flask(__name__)

# Initialize Breeze API (commented for now — add credentials in Render Environment later)
try:
    breeze = Breeze(api_key=os.getenv("BREEZE_API_KEY"))
    print("✅ Breeze API initialized")
except Exception as e:
    print("⚠️ Breeze initialization skipped or failed:", e)

@app.route("/chartink", methods=["POST"])
def chartink_webhook():
    try:
        data = request.get_json(force=True)
        print(f"[{datetime.now()}] ✅ Received Chartink alert: {data}")

        if not data:
            return jsonify({"error": "Empty payload"}), 400

        # Handle single or multiple stock payloads
        symbols = []
        if isinstance(data, list):
            symbols = [item.get("symbol") for item in data if "symbol" in item]
        elif isinstance(data, dict) and "symbol" in data:
            symbols = [data["symbol"]]

        print(f"🧾 Stocks to trade (test mode): {symbols}")

        for symbol in symbols:
            print(f"💡 Simulated order for {symbol}: {{'stock': '{symbol}', 'status': 'simulated - no order placed'}}")

            # === Actual Breeze Order (commented out for safety) ===
            # breeze.place_order(
            #     stock_code=symbol,
            #     exchange_code="NSE",
            #     product="margin",
            #     action="buy",
            #     order_type="market",
            #     quantity="1",
            #     price="0",
            #     validity="day"
            # )
            # print(f"✅ Real order placed for {symbol}")

        return jsonify({"status": "success", "received": len(symbols)})

    except Exception as e:
        print(f"❌ Error handling webhook: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return "Chartink Webhook Receiver is running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
