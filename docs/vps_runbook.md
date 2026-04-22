# VPS Runbook（yui-quant-lab / Ubuntu 24.04）

目標：在 Hostinger VPS 上以 **Nginx（80）→ Gunicorn（127.0.0.1:8000）→ Flask** 常駐運行，並用 **systemd** 開機自啟、崩潰重啟；環境變數**只**由專案根目錄的 `.env` 經 **python-dotenv** 載入（**勿**在 systemd 裡再用 `EnvironmentFile` 重複注入同一批 secret）。

---

## 0) 架構（正式）

| 元件 | 角色 |
|------|------|
| **Nginx** | 對外聽 **80**（之後可加 443）；`client_max_body_size` 避免 JSON 被 413 擋下；反向代理到本機 Gunicorn。 |
| **Gunicorn** | WSGI；**固定 `--workers 1`**（`output/state.json`、JSONL 為檔案型，多 worker 會並發寫入競態）。 |
| **Flask（app.py）** | `/health`、`/tv-webhook` 等；啟動時 `load_dotenv(.env)`。 |
| **systemd** | 管理 Gunicorn 行程、`journalctl` 看 stdout/stderr（含 **raw_request** 除錯列）。 |

Repo 範本：

- [`deploy/yui-quant-lab.service`](../deploy/yui-quant-lab.service)
- [`deploy/nginx-yui-quant-lab.conf`](../deploy/nginx-yui-quant-lab.conf)
- [`deploy/nginx-yui-quant-lab-zones.conf`](../deploy/nginx-yui-quant-lab-zones.conf)（`limit_req_zone`，需放在 `http` 層）

---

## 1) 專案目錄與 venv

```bash
cd ~/yui-quant-lab
pwd
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

---

## 2) 環境變數（唯一來源：`.env`）

```bash
cd ~/yui-quant-lab
cp .env.example .env
chmod 600 .env
nano .env
```

至少：`TV_WEBHOOK_SECRET`、`ENABLE_TELEGRAM_NOTIFY`（若要真送）、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。

**systemd**：`WorkingDirectory` 必須是專案根（與 `.env` 同目錄），**不要**在 unit 裡加 `EnvironmentFile=`。

備份：

```bash
cp .env ".env.backup.$(date +%Y%m%d_%H%M%S)"
```

---

## 3) systemd（Gunicorn）

1. 複製範本並把 `CHANGEME` 改成你的 Linux 使用者與路徑：

   ```bash
   sudo cp deploy/yui-quant-lab.service /etc/systemd/system/yui-quant-lab.service
   sudo nano /etc/systemd/system/yui-quant-lab.service
   ```

2. 啟用並啟動：

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now yui-quant-lab.service
   sudo systemctl status yui-quant-lab.service --no-pager
   ```

3. 日誌（含 Gunicorn access、應用 `print`、**raw_request** JSON 行）：

   ```bash
   journalctl -u yui-quant-lab.service -n 200 --no-pager
   journalctl -u yui-quant-lab.service -f
   ```

**`--workers 1`**：必須保留；多 worker 可能同時改寫 `output/state.json` 與日誌檔，造成難以重現的錯誤。

---

## 4) Nginx

**兩步驟（順序重要）**：先把 `limit_req_zone` 放到 `conf.d`，再啟用 site。

```bash
sudo cp deploy/nginx-yui-quant-lab-zones.conf /etc/nginx/conf.d/yui-quant-lab-zones.conf
sudo cp deploy/nginx-yui-quant-lab.conf /etc/nginx/sites-available/yui-quant-lab
sudo ln -sf /etc/nginx/sites-available/yui-quant-lab /etc/nginx/sites-enabled/yui-quant-lab
sudo nginx -t && sudo systemctl reload nginx
```

`client_max_body_size 256k` 可依需求改大（例如 `1m`）。過大請求會回 **413**，可用於驗證此設定是否生效。

**Rate limit 驗收（可選）**：對 `/tv-webhook` 連續大量 `curl`，預期可能出現 **503**（burst 與漏桶排隊耗盡時）；正常單次 alert 不應被擋。

**stdout raw_request**：可在 `.env` 設定 `RAW_REQUEST_LOG_MODE=metadata_only`（只記路徑/IP/長度）或 `off`（完全不印 raw_request），降低 `journalctl` 體積。

---

## 5) 最小驗收（curl）

經 **Nginx → Gunicorn**（本機）：

```bash
curl -sS http://127.0.0.1/health
```

只測 Gunicorn（繞過 Nginx）：

```bash
curl -sS http://127.0.0.1:8000/health
```

`tv-webhook`（建議用 Python 讀 `.env`，避免 shell 轉義弄壞 `secret`）：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate

TVS=$(python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('TV_WEBHOOK_SECRET',''))")
BODY="{\"secret\":\"$TVS\",\"symbol\":\"MNQ\",\"signal\":\"long_breakout\",\"price\":19010,\"breakout_level\":18990,\"delta_strength\":0.92}"

curl -sS -X POST http://127.0.0.1/tv-webhook \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

預期：HTTP **200**，JSON 內 `ok: true`。同時在 `journalctl -u yui-quant-lab -f` 可看到一行 **`"event":"raw_request"`**（`/tv-webhook` 的 `body_preview` 內 `secret` 已遮罩；若 `RAW_REQUEST_LOG_MODE=metadata_only` 則不會有 `body_preview`）。

**Webhook 短時去重**：用同一個 `BODY` 連送兩次 `POST /tv-webhook`，第二次預期仍 **200**，但 JSON 內 `duplicate: true`（且 `output/signal_log.jsonl` 不應再出現一組新的決策鏈）。去重狀態檔：`output/webhook_dedupe.json`（已被 `.gitignore` 忽略）。

對外公網（另一台機器）：

```bash
curl -sS http://<PUBLIC_IP>/health
```

TradingView 僅允許 **80/443**；URL 形如 `http://<PUBLIC_IP>/tv-webhook`。

---

## 6) 端到端驗收（分層）

| 層級 | 驗什麼 | 怎麼驗 |
|------|--------|--------|
| **TradingView** | 真 alert 打到對外 URL | TV 後台 webhook delivery / 重送；URL `http://<IP或網域>/tv-webhook`。 |
| **Nginx** | 80 轉發、body 上限 | `curl -i http://127.0.0.1/health`；`sudo nginx -t`；`/var/log/nginx/error.log`；故意送超大 body 看 **413**。 |
| **Gunicorn** | 聽 8000、單 worker | `curl http://127.0.0.1:8000/health`；`ss -ltnp` 看 `8000`；行程應符合 **workers=1**。 |
| **Flask** | 路由、secret、raw log | 上一節 `curl`；`journalctl -u yui-quant-lab -f` 看 **raw_request** 與錯誤訊息。 |
| **JSONL** | 決策鏈寫入 | `tail -f output/signal_log.jsonl`；同一 `request_id`：`tv_webhook_received` → `decision_result` 等。 |
| **Telegram** | 通知、失敗不擋 HTTP | 客戶端是否收到；API 失敗時仍應 HTTP 200；可選 `python scripts/run_telegram_decision_smoke.py telegram`。 |

---

## 7) Telegram 最小驗收

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
python scripts/run_telegram_decision_smoke.py telegram
```

---

## 8) 本機一鍵鏈路（可選）

專案根目錄、**服務已在本機或 VPS 上跑**（預設打 `http://127.0.0.1`，即 Nginx 的 80）：

```bash
python scripts/run_live_chain_check.py
```

若只跑 `python app.py`（預設 **5000**），請先匯出：

```bash
export WEBHOOK_BASE_URL=http://127.0.0.1:5000
python scripts/run_live_chain_check.py
```

---

## 9) 業務日誌（JSONL）

```bash
tail -n 30 output/signal_log.jsonl
tail -f output/signal_log.jsonl
```

---

## 10) 緊急除錯（非正式）

僅供臨時本機／排錯，**不要**當正式上線方式：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
export FLASK_DEV_PORT=5000
python app.py
```

正式環境請始終使用 **systemd + Gunicorn + Nginx**。

---

## 11) 常見問題

### A) `curl http://127.0.0.1/health` 連不上

```bash
systemctl is-active yui-quant-lab.service
systemctl is-active nginx
ss -ltnp | head
journalctl -u yui-quant-lab.service -n 80 --no-pager
sudo tail -n 50 /var/log/nginx/error.log
```

### B) `invalid_secret`

TradingView JSON 的 `secret` 與 `.env` 的 `TV_WEBHOOK_SECRET` 不一致。

### C) `bad_payload`

- 未帶 `Content-Type: application/json`
- 內容不是合法 JSON 物件

### D) 413 Request Entity Too Large

Nginx `client_max_body_size` 過小；調大後 `nginx -t` 並 `reload`。

---

## 12) Rollback（最小）

```bash
sudo systemctl stop yui-quant-lab.service
sudo systemctl disable yui-quant-lab.service
```

還原 `.env`、程式用 Git；必要時移除 `sites-enabled` 裡的 site 連結後 `nginx -t && reload`。
