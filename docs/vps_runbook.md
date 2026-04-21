# VPS Runbook（yui-quant-lab / Ubuntu 24.04）

本文件目標：讓你在 VPS 上能用「最少指令」完成啟動、驗收、重啟、看 log、清 log。  
本文件刻意不包含 nginx / HTTPS / domain / Docker / systemd。

## 0) 角色分工

- 本機：開發、測試、Git 版本管理
- VPS：部署驗證、長時間運行

## 1) 專案位置與 venv

你目前實際路徑範例（依你機器為準）：

```bash
cd ~/yui-quant-lab
pwd
```

啟用 venv：

```bash
source .venv/bin/activate
which python
python -c "import sys; print(sys.executable)"
```

合格判斷：

- `which python` 應指向 `.../yui-quant-lab/.venv/bin/python`

安裝依賴（首次或 requirements 變更後）：

```bash
pip install -r requirements.txt
```

## 2) 環境變數（`.env`）

在專案根目錄建立 `.env`（不要提交到 Git）：

```bash
cd ~/yui-quant-lab
cp .env.example .env
nano .env
```

至少需要：

- `TV_WEBHOOK_SECRET`：TradingView alert JSON 內 `secret` 欄位要一致
- `ENABLE_TELEGRAM_NOTIFY=true`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

備份 `.env`（建議每次大改前做一次）：

```bash
cp .env ".env.backup.$(date +%Y%m%d_%H%M%S)"
```

## 3) 啟動 app（port 80 + debug 關）

確認 `app.py` 最後啟動段為：

- `host="0.0.0.0"`
- `port=80`
- `debug=False`

啟動（背景 + log）：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
nohup ./.venv/bin/python app.py > app.log 2>&1 &
```

停止：

```bash
pkill -f "python app.py"
```

重啟（一鍵）：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
pkill -f "python app.py"; nohup ./.venv/bin/python app.py > app.log 2>&1 &
```

確認程序存在：

```bash
ps -ef | grep "python app.py" | grep -v grep
```

確認監聽 port（應看到 `:80`）：

```bash
ss -ltnp | grep -E ":80|:5000"
```

## 4) 最小驗收（TradingView 限制：80/443）

健康檢查：

```bash
curl http://127.0.0.1/health
```

`tv-webhook`（建議用 Python 讀 `.env`，避免 shell 跳脫問題）：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate

TVS=$(python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('TV_WEBHOOK_SECRET',''))")
BODY="{\"secret\":\"$TVS\",\"symbol\":\"MNQ\",\"signal\":\"long_breakout\",\"price\":19010,\"breakout_level\":18990,\"delta_strength\":0.92}"

printf '%s\n' "$BODY" | python -m json.tool

curl -X POST http://127.0.0.1/tv-webhook \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

預期：

- HTTP 200
- JSON 內 `ok: true`

從**另一台電腦**對外公網驗證（把 `<PUBLIC_IP>` 換成 VPS IP）：

```bash
curl http://<PUBLIC_IP>/health
```

## 5) Telegram 最小驗收

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
python scripts/run_telegram_decision_smoke.py telegram
```

預期：

- `ok: True`
- `status_code: 200`
- `mode: telegram`

## 6) 看 log（兩條線）

應用 stdout/stderr：

```bash
tail -n 80 app.log
tail -f app.log
```

業務事件 JSONL：

```bash
tail -n 20 output/signal_log.jsonl
```

## 7) 清 log（最小：truncate）

```bash
cd ~/yui-quant-lab
ls -lh app.log
truncate -s 0 app.log

ls -lh output/signal_log.jsonl
truncate -s 0 output/signal_log.jsonl
```

## 8) 常見問題（最短排查）

### A) `curl http://127.0.0.1/health` 連不上

```bash
ss -ltnp | grep -E ":80|:5000"
tail -n 80 app.log
```

### B) `tv-webhook` 回 `invalid_secret`

代表 TradingView JSON 的 `secret` 與 `.env` 的 `TV_WEBHOOK_SECRET` 不一致。

### C) `tv-webhook` 回 `bad_payload`

常見原因：

- 沒有 `Content-Type: application/json`
- JSON 不是合法物件（可用 `python -m json.tool` 先驗證）

### D) 你用 `grep/cut` 讀 secret 造成 JSON 壞掉

改用本文件的 Python 讀法（第 4 節）。

## 9) Rollback（最小）

- 停服務：`pkill -f "python app.py"`
- 還原 `.env`：把備份檔改回 `.env`
- 還原程式：用 Git 回到上一個可用 commit（本機操作後再同步到 VPS）
