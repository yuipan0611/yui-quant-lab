# Repo Map（Repo 結構盤點）

本文盤點 `yui-quant-lab` 目前 repo 的主要檔案與資料夾用途。這份文件用來協助 debug、維運與後續整理，不代表檔案已被搬移或重構。

## Top-level（頂層結構）

```text
yui-quant-lab/
├─ app.py
├─ decision_engine.py
├─ state_manager.py
├─ command_writer.py
├─ execution_tracker.py
├─ telegram_bot.py
├─ webhook_dedupe.py
├─ time_utils.py
├─ replay.py
├─ e2e_full_flow.py
├─ deploy/
├─ docs/
├─ fixtures/
├─ output/
├─ scripts/
├─ tests/
├─ .env
├─ .env.example
├─ .gitignore
├─ requirements.txt
└─ README.md
```

中文說明：目前 production modules 仍放在 repo root。這讓 `app.py`、systemd `app:app`、測試與 scripts 可以直接用 root-level import。第一階段整理應以文件為主，不搬動這些檔案。

## Production Path（正式執行路徑）

| Path | Purpose | Notes |
|---|---|---|
| `app.py` | Flask production entrypoint | 定義 `GET /health`、`POST /webhook`、`POST /tv-webhook`、`POST /fill-result`、`POST /order-event`、`POST /telegram/webhook`。Production service 目前使用 `app:app`。 |
| `decision_engine.py` | Trading decision engine | 當 state gate 通過時，由 `app.process_webhook_payload()` 呼叫 `decide_trade()`。 |
| `state_manager.py` | File-based trading state and risk gate | 讀寫 `output/state.json`，處理 daily reset、state gate、decision effects、fill effects、fill dedupe。 |
| `command_writer.py` | Command and signal log writer | 寫入 `output/order_command.json`，追加 `output/signal_log.jsonl`。 |
| `execution_tracker.py` | Order lifecycle tracking | 建立 per-order records，並處理 broker order/fill lifecycle，輸出到 `output/orders/`。 |
| `telegram_bot.py` | Telegram notification and command integration | 發送 decision/fill notifications，並處理 Telegram webhook updates。 |
| `webhook_dedupe.py` | Webhook idempotency helper | 使用 `output/webhook_dedupe.json` 或 `WEBHOOK_DEDUPE_PATH` 進行短時間去重。 |
| `time_utils.py` | Shared Taipei time helpers | 被多個 production modules 共用。 |

中文說明：上述檔案是正式交易流程中會被直接或間接呼叫的核心。整理時要優先保護 import path、runtime path 與 deployment path。

## Test, Replay, and Demo Support（測試、重播與示範）

| Path | Purpose | Notes |
|---|---|---|
| `tests/` | Automated test suite | 目前混合 unit、integration、smoke、e2e tests。 |
| `tests/helpers.py` | Test helper payload builders | 測試輔助，不屬於 production code。 |
| `fixtures/` | Replay fixture payloads | 被 `replay.py` 與 replay smoke tests 使用。 |
| `replay.py` | Fixture replay CLI | 用於 regression/smoke checks；不在 production service request path。 |
| `e2e_full_flow.py` | In-process e2e helper and CLI | 被 tests 與 `scripts/run_e2e_demo.py` 使用；不屬於 production service。 |

中文說明：這些檔案可以協助驗證正式流程，但不應和 production runtime 混為一談。後續若要搬檔，應先確認 tests 與 scripts 的 import。

## Deployment and Operations（部署與維運）

| Path | Purpose | Notes |
|---|---|---|
| `deploy/yui-quant-lab.service` | systemd service template | 啟動 Gunicorn 並指向 `app:app`；VPS 路徑 placeholders 必須與實際部署目錄一致。 |
| `deploy/nginx-yui-quant-lab.conf` | nginx site proxy config | 將 webhook routes proxy 到 Gunicorn。 |
| `deploy/nginx-yui-quant-lab-zones.conf` | nginx rate-limit zone config | 必須在 nginx `http` context 載入，且早於 site config。 |
| `scripts/` | Operational scripts | 目前混合 deploy、checks、maintenance、demo scripts。 |
| `docs/` | Architecture, deployment, runbook, and planning docs | 文件用途，不改變 runtime behavior。 |
| `docs/ops/` | Production acceptance evidence | 保存 service、nginx、health、journal outputs。 |

中文說明：deployment files 目前和 root layout 綁定。尤其 `deploy/yui-quant-lab.service` 依賴 `app:app`，不能在未同步部署設定前搬動 `app.py`。

## Protected Areas（暫時不可重構區域）

以下區域與 deployment 強耦合，Phase 1/2 不應修改：

- `app.py`（systemd `app:app` entry）
- root-level modules（import path 依賴）
- `deploy/`（systemd + nginx config）
- `output/`（runtime state）

中文說明：這些區域直接影響 production startup、webhook route、state persistence 與 execution handoff。整理 repo 時應先補文件與標註，等測試與部署路徑都確認後再考慮重構。

## Runtime and Local Artifacts（Runtime 與本機產物）

| Path | Purpose | Git policy |
|---|---|---|
| `output/` | Runtime state, logs, commands, dedupe, order records | Ignored except `output/.gitkeep`。 |
| `.env` | Local secrets/config | Ignored。 |
| `.env.example` | Config template | Tracked。 |
| `yui-quant-lab-upload.tgz` | Local deploy/upload artifact | Ignored。 |
| `__pycache__/` | Python bytecode cache | Ignored。 |

中文說明：`output/` 是 production runtime 狀態來源之一，不是 fixtures。清理或搬移前要先確認服務是否仍在寫入。

## Suggested Classification（建議歸類）

| Current area | Suggested role |
|---|---|
| Root production modules | 目前先保留原位。搬動可能破壞 imports 與 deployment。 |
| `scripts/` | 後續可拆成 `scripts/deploy/`、`scripts/checks/`、`scripts/maintenance/`、`scripts/one_off/`。 |
| `tests/` | 後續可拆成 `tests/unit/`、`tests/integration/`、`tests/e2e/`。拆之前要先確認 import 與 test runner。 |
| `docs/` | 可立即擴充，是第一階段最安全的整理範圍。 |
| `output/` | Runtime only；不要 commit generated files。 |

## Notes

- 本文件描述的是現況（as-is）。
- 本文件不是重構設計（not target architecture）。
