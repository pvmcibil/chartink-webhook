from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)


@app.route("/chartink", methods=["POST"])
def chartink_webhook():
    data = request.get_json(force=True)

    # Log with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Received alert: {data}")

    # Optionally save to file (Render will persist it for this deployment)
    with open("chartink_alerts.log", "a") as f:
        f.write(f"[{timestamp}] {data}\n")

    return jsonify({"status": "success", "received": len(data or {})})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
