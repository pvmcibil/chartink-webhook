from flask import Flask, request, jsonify
from datetime import datetime
import os
from breeze_connect import Breeze
import json

app = Flask(__name__)

# Initialize Breeze connection (won’t place any order now)
breeze = Breeze(api_key=os.getenv("BREEZE_API_KEY"))
breeze.generate_session(
    api_secret=os.getenv("BREEZE_API_SECRET"),
    session_token=os.getenv("BREEZE_SESSION_TOKEN")
)

@app.route("/chartink", methods=["POST"])
def chartink_webhook():
    try:
        data = request.get_json(force=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"[{timestamp}] ✅ Received Chartink alert: {data}", flush=True)

        # Normalize data
        stocks = []
        if isinstance(data, dict):
            # Case 1: Single stock {"symbol": "RELIANCE", "price": 2700}
            if "symbol" in data:
                stocks.append(data["symbol"])
        elif isinstance(data, list):
            # Case 2: List of stocks [{"symbol": "RELIANCE"}, {"symbol": "HDFCBANK"}]
            stocks = [item["symbol"] for item in data if "symbol" in item]

        print(f"🧾 Stocks to trade (test mode): {stocks}", flush=True)

        results = []
        for stock in stocks:
            try:
                # --- Commented out actual order placement for testing ---
                # order_resp = breeze.place_order(
                #     stock_code=stock,
                #     exchange_code="NSE",
                #     product="margin",           # Intraday
                #     action="buy",                # or dynamic
                #     order_type="market",         # Market order
                #     stoploss="",                 # No SL
                #     quantity="1",
                #     price="0",                   # Ignored for market
                #     validity="day"
                # )
                # results.append({stock: order_resp})
                # print(f"Order placed for {stock}: {order_resp}", flush=True)

                # Instead, just log a fake response
                fake_resp = {"stock": stock, "status": "simulated - no order placed"}
                results.append(fake_resp)
                print(f"💡 Simulated order for {stock}: {fake_resp}", flush=True)

            except Exception as e:
                results.append({stock: f"Error: {str(e)}"})
                print(f"⚠️ Failed to simulate order for {stock}: {e}", flush=True)

        # Log results to file
        with open("chartink_orders.log", "a") as f:
            f.write(f"[{timestamp}] {json.dumps(results)}\n")

        return jsonify({"status": "success (test mode)", "orders": results})

    except Exception as e:
        print(f"❌ Error in webhook: {e}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
