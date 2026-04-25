# Runbook（維運手冊）

本文是 `yui-quant-lab` 的日常操作索引。更完整的 VPS 部署與驗收步驟仍以既有文件為主：

- `docs/vps_runbook.md`
- `docs/vps_acceptance_runbook.md`
- `docs/deployment.md`

## Daily Health Checks（日常健康檢查）

```bash
curl -i http://127.0.0.1/health
curl -i http://127.0.0.1:8000/health
curl -i http://<PUBLIC_IP>/health
```

中文說明：先確認 nginx、Gunicorn 與 Flask service 是否可用，再判斷 TradingView 或 Telegram 是否異常。預期結果是 HTTP 200 與簡短 JSON body。

## Production Service（正式服務）

```text
Nginx :80
  -> Gunicorn 127.0.0.1:8000
  -> Flask app:app
```

中文說明：目前 production 是 nginx 對外接流量，proxy 到本機 Gunicorn，再由 Gunicorn 載入 Flask `app:app`。因此 `app.py` 的位置與 systemd `ExecStart` 必須保持一致。

The systemd service template is:

```text
deploy/yui-quant-lab.service
```

中文說明：`deploy/yui-quant-lab.service` 是 service template。套用到 VPS 前，需要確認 `User`、`Group`、`WorkingDirectory`、`.venv` 與 `ExecStart` 路徑。

Important service assumptions:

| Item | Requirement |
|---|---|
| `WorkingDirectory` | Repo root on the VPS. |
| `ExecStart` | Gunicorn should point to `app:app`. |
| `.env` | Located in repo root. |
| Worker count | Keep `--workers 1` while using file-based `output/` state. |

中文說明：`--workers 1` 是目前 file-based state 設計的重要限制。多 worker 可能同時寫入 `output/state.json`、`output/signal_log.jsonl` 或 order records，增加 race condition 風險。

## Nginx

Nginx config files:

```text
deploy/nginx-yui-quant-lab-zones.conf
deploy/nginx-yui-quant-lab.conf
```

中文說明：`deploy/nginx-yui-quant-lab-zones.conf` 定義 rate limit zone，必須先載入。`deploy/nginx-yui-quant-lab.conf` 是 site proxy config，會引用該 zone。

Common checks:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl is-active nginx
```

中文說明：修改 nginx config 後先跑 `sudo nginx -t`。測試通過後再 reload，避免把可用的 webhook route 重新載入成壞設定。

## Environment（環境變數）

Runtime configuration is loaded from root `.env` by `app.py`.

中文說明：目前環境設定由 repo root 的 `.env` 作為 single source。不要把 secrets commit 到 git，也不要在未確認來源時同時用多套 secret injection。

Important values:

| Variable | Purpose |
|---|---|
| `TV_WEBHOOK_SECRET` | Secret expected by `POST /tv-webhook`. |
| `ENABLE_TELEGRAM_NOTIFY` | Enables or disables decision/fill notifications. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot API token. |
| `TELEGRAM_CHAT_ID` | Authorized notification chat id. |
| `TELEGRAM_WEBHOOK_SECRET` | Secret header expected for Telegram webhook, if configured. |
| `WEBHOOK_DEDUPE_TTL_SEC` | Dedupe TTL for repeated webhook payloads. |
| `WEBHOOK_DEDUPE_PATH` | Optional override for dedupe store path. |
| `RAW_REQUEST_LOG_MODE` | Controls raw request logging verbosity. |

Do not commit `.env`.

## Useful Scripts（常用 Scripts）

Current scripts are still in a flat `scripts/` directory.

中文說明：目前 scripts 還沒有分資料夾。執行前要先確認 script 是否會打 live endpoint、改 `.env`、reload service 或修改 VPS。

| Script | Use |
|---|---|
| `scripts/bootstrap_vps.sh` | One-shot VPS bootstrap/deploy。高影響操作，需謹慎使用。 |
| `scripts/run_live_chain_check.py` | Live health + TV webhook + Telegram chain check。 |
| `scripts/run_telegram_decision_smoke.py` | Telegram decision notification smoke test。 |
| `scripts/run_e2e_demo.py` | Local in-process e2e demo。 |
| `scripts/setup_env.ps1` | Interactive local `.env` setup。 |
| `scripts/sync_tv_webhook_secret_to_vps.sh` | Sync local TV secret to VPS `.env`。 |
| `scripts/sync_tv_webhook_secret_to_vps.ps1` | Windows PowerShell version of secret sync。 |
| `scripts/verify_tv_webhook.ps1` | Live `POST /tv-webhook` verification。 |
| `scripts/vps_restart_app.sh` | VPS service discovery and restart guidance。 |

Future cleanup can split these into:

```text
scripts/deploy/
scripts/checks/
scripts/maintenance/
scripts/one_off/
```

中文說明：這是建議分類，不代表目前已搬移。若真的搬 scripts，需要同步更新 docs、runbooks 與任何人工操作習慣。

## Runtime Files（Runtime 檔案）

Runtime files live under `output/` and are intentionally ignored by git except `output/.gitkeep`.

中文說明：`output/` 裡的檔案是服務執行中的狀態與 log，不是 source code。不要把這些檔案加入 git。

Important files:

| Path | Purpose |
|---|---|
| `output/state.json` | Current trading state. |
| `output/order_command.json` | Latest command for execution layer. |
| `output/signal_log.jsonl` | Webhook and decision log. |
| `output/execution_events.jsonl` | Execution lifecycle event log. |
| `output/webhook_dedupe.json` | Recent webhook fingerprint store. |
| `output/manual_trade_lock.json` | Manual lock state controlled by Telegram. |
| `output/orders/*.json` | Per-order lifecycle records. |

## Safe First Response to Incidents（事故初步處理）

1. Check `GET /health`.
2. Check systemd service status.
3. Check nginx status and config test.
4. Check `.env` is present and has the expected secret values.
5. Check recent journal logs for raw request or traceback output.
6. Check `output/state.json` and `output/signal_log.jsonl` carefully before changing anything.

中文說明：事故處理先觀察，不要急著搬檔案或改 imports。若 production 正在運作，任何對 `output/`、`.env`、systemd 或 nginx 的變更都應先確認影響範圍。

Avoid moving files or changing imports during an incident unless the failure is already isolated to that change.

## Common Mistakes（常見錯誤）

- 在 production 直接修改 `app.py` 或 imports
- 在服務運行時清空 `output/state.json`
- 同時使用多個 `.env` 來源
- 在未測試 nginx config 下 reload
- 使用 `--workers > 1`（目前設計不支援）

中文說明：這些操作是最常導致 webhook / state / execution 錯誤的原因。

## Notes

- 本文件描述的是現況（as-is）。
- 本文件不是重構設計（not target architecture）。
