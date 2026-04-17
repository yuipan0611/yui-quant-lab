from flask import Flask, request, jsonify

app = Flask(__name__)

# TradingView alert 必填欄位
REQUIRED_FIELDS = [
    "symbol",
    "signal",
    "price",
    "breakout_level",
    "delta_strength",
]


@app.route("/health", methods=["GET"])
def health():
    # 健康檢查端點
    return jsonify({"status": "ok"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    # 安全解析 JSON，避免非 JSON 直接丟例外
    payload = request.get_json(silent=True)

    if payload is None:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid or missing JSON body. Please send valid JSON with Content-Type: application/json.",
                }
            ),
            400,
        )

    # 只接受 JSON 物件（key-value）
    if not isinstance(payload, dict):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "JSON payload must be an object.",
                }
            ),
            400,
        )

    # 驗證必要欄位是否齊全
    missing_fields = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing_fields:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing required fields.",
                    "missing_fields": missing_fields,
                    "required_fields": REQUIRED_FIELDS,
                }
            ),
            400,
        )

    # 成功時印出接收到的資料（最小可用版）
    print("=== TradingView Alert Received ===")
    print(f"symbol: {payload['symbol']}")
    print(f"signal: {payload['signal']}")
    print(f"price: {payload['price']}")
    print(f"breakout_level: {payload['breakout_level']}")
    print(f"delta_strength: {payload['delta_strength']}")
    print(f"raw_payload: {payload}")

    return (
        jsonify(
            {
                "status": "success",
                "message": "Webhook received successfully.",
                "data": payload,
            }
        ),
        200,
    )


if __name__ == "__main__":
    # 本機測試用；正式上線建議關閉 debug
    app.run(host="0.0.0.0", port=5000, debug=True)
