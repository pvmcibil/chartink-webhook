from flask import Flask, request, jsonify
import datetime
import json
import os

app = Flask(__name__)

@app.route("/chartink", methods=["POST"])
def chartink_webhook():
    data = request.get_json(force=True, silent=True)

    if not data:
        return jsonify({"error": "Invalid or empty payload"}), 400

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== Chartink Alert @ {timestamp} ===")

    output = []
    for stock in data.get("stocks", []):
        name = stock.get("name")
        price = stock.get("price")
        volume = stock.get("volume")
        nse_code = f"{name}.NS"

        info = {
            "timestamp": timestamp,
            "scan_name": data.get("scan_name", "Unknown Scan"),
            "stock_name": name,
            "nse_code": nse_code,
            "price": price,
            "volume": volume,
        }

        output.append(info)
        print(f"→ {name} | {nse_code} | ₹{price} | Vol: {volume}")

    # Optional: store logs in Render’s /tmp directory
    with open("/tmp/chartink_logs.jsonl", "a") as f:
        for o in output:
            f.write(json.dumps(o) + "\n")

    return jsonify({"status": "success", "received": len(output)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render uses dynamic ports
    app.run(host="0.0.0.0", port=port)
