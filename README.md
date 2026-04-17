# yui-quant-lab

Minimal Flask webhook server for receiving TradingView alerts.

## What this project does

- `GET /health`: returns server status
- `POST /webhook`: receives TradingView alert JSON
- Validates required fields:
  - `symbol`
  - `signal`
  - `price`
  - `breakout_level`
  - `delta_strength`

## Project files

- `app.py`: webhook server
- `requirements.txt`: Python dependencies
- `.gitignore`: ignored local/system files

## Setup (Windows PowerShell)

```powershell
cd C:\Users\user\.cursor\projects\yui-quant-lab
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python app.py
```

Server runs at:

- `http://127.0.0.1:5000`

## Quick test

Health check:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5000/health"
```

Webhook test:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5000/webhook" -ContentType "application/json" -Body '{"symbol":"BTCUSDT","signal":"BUY","price":68000,"breakout_level":67500,"delta_strength":1.2}'
```

## TradingView webhook settings

- Webhook URL:
  - `https://<your-ngrok-domain>/webhook`
- Alert Message (JSON):

```json
{
  "symbol": "{{ticker}}",
  "signal": "BUY",
  "price": {{close}},
  "breakout_level": 0,
  "delta_strength": 0
}
```

## Common errors

- `405 Method Not Allowed`: you opened `/webhook` with GET in browser. `/webhook` accepts POST only.
- `400 Bad Request`: invalid JSON or missing required fields.
- `ERR_CONNECTION_REFUSED`: Flask server is not running on port `5000`.
