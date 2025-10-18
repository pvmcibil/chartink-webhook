from flask import Flask, request, jsonify
import json, os
from datetime import datetime

# Breeze API (commented for testing)
# from breeze_connect import BreezeConnect

app = Flask(__name__)

# ============= Breeze Setup (Uncomment later for live trading) =============
# breeze = BreezeConnect(api_key=os.getenv("BREEZE_API_KEY"))
# breeze.generate_session(api_secret=os.getenv("BREEZE_API_SECRET"),
#                         session_token=os.getenv("BREEZE_SESSION_TOKEN"))

# JSON file to track open trades
OPEN_TRADES_FILE = "open_trades.json"

# Helper: safely load JSON
def load_trades():
    if not os.path.exists(OPEN_TRADES_FILE):
        return []
    with open(OPEN_TRADES_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

# Helper: save JSON
def save_trades(trades):
    with open(OPEN_TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=4)

@app.route('/chartink', methods=['POST'])
def chartink_webhook():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or empty payload"}), 400

    print(f"[{datetime.now()}] Received alert: {data}")

    trades = load_trades()

    for stock in data:
        symbol = stock.get("symbol")
        if not symbol:
            continue

        # ----- PLACE BUY ORDER -----
        print(f"Placing buy order for {symbol} (TEST MODE)")
        # response = breeze.place_order(
        #     stock_code=symbol,
        #     exchange_code="NSE",
        #     product="margin",
        #     action="buy",
        #     order_type="market",
        #     quantity="1",
        #     price="",
        #     validity="day"
        # )
        # print("Buy order response:", response)

        # Simulated buy price
        buy_price = 1000.0  

        trades.append({
            "symbol": symbol,
            "buy_price": buy_price,
            "buy_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

    save_trades(trades)
    return jsonify({"status": "success", "received": len(data)}), 200

@app.route('/')
def home():
    return "Chartink Webhook Receiver Active"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
