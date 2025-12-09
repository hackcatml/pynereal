from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("📥 Received Webhook:", data)
    return jsonify({"status": "ok", "received": data}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8884)