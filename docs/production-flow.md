# Production Flow（正式流程）

## System Overview（系統概覽）

`yui-quant-lab` 是一個 event-driven trading system。正式流程由外部事件觸發：TradingView 送出 webhook，Flask 接收並驗證 payload，系統讀取目前 state，通過風控 gate 後呼叫 decision engine，必要時寫出 execution command，最後記錄 log 並發送 Telegram notification。

核心鏈路可以簡化為：

```text
webhook -> state -> decision -> execution -> notification
```

中文說明：這代表系統不是長輪詢策略引擎，而是由 webhook event 驅動。每次事件都會依據當下 `output/state.json` 與 webhook payload 產生一次 decision。

## Main TradingView Flow（TradingView 主流程）

```text
TradingView alert
  -> POST /tv-webhook
  -> app.py validates TV_WEBHOOK_SECRET
  -> webhook_dedupe.check_and_remember("/tv-webhook", body)
  -> app.adapt_tv_payload()
  -> app.process_webhook_payload()
  -> state_manager.load_state()
  -> state_manager.reset_state_if_new_day()
  -> state_manager.evaluate_state_gate()
  -> decision_engine.decide_trade()
  -> command_writer.write_order_command() if CHASE or RETEST
  -> execution_tracker.create_order_record() if command write succeeds
  -> state_manager.apply_decision_effects()
  -> state_manager.save_state()
  -> command_writer.append_signal_log()
  -> telegram_bot.notify_decision()
  -> HTTP 200 response
```

中文說明：`POST /tv-webhook` 是 TradingView 對外入口。`app.py` 先驗證 `TV_WEBHOOK_SECRET`，再用 `webhook_dedupe.check_and_remember()` 避免短時間重複處理相同 payload。正式 decision 由 `app.process_webhook_payload()` 串起 state、decision、command、log 與 notification。`state_manager.evaluate_state_gate()` 是第一層風控與交易過濾。

## Important Routes（重要 Routes）

| Route | File | Purpose |
|---|---|---|
| `GET /health` | `app.py` | 給 nginx、Gunicorn 與部署驗收使用的 health check。 |
| `POST /tv-webhook` | `app.py` | TradingView-facing webhook，包含 secret validation 與 TV payload adapter。 |
| `POST /webhook` | `app.py` | Internal/direct webhook，與 `POST /tv-webhook` 共用核心處理流程。 |
| `POST /fill-result` | `app.py` | 接收 fill/PnL result，更新 state 與 order lifecycle。 |
| `POST /order-event` | `app.py` | 接收 broker/order lifecycle events。 |
| `POST /telegram/webhook` | `app.py`, `telegram_bot.py` | 接收 Telegram commands 與 callbacks。 |

中文說明：`POST /tv-webhook` 和 `POST /webhook` 都會進入 `app.process_webhook_payload()`。`POST /fill-result` 與 `POST /order-event` 是 execution layer 回寫狀態的入口。

## Module Participation（Module 參與狀態）

| File | Called in production? | How it participates |
|---|---:|---|
| `app.py` | Yes | Flask app、routes、request validation、流程編排。 |
| `decision_engine.py` | Yes | `decide_trade()` 回傳 `decision`、`reason`、`plan`、`trace`。 |
| `state_manager.py` | Yes | State load/reset/gate/save、decision effects、fill effects、fill dedupe。 |
| `command_writer.py` | Yes | 寫入 order command file 與 signal JSONL log。 |
| `execution_tracker.py` | Yes | 建立 order record、處理 order-event、連結 fill-to-order。 |
| `telegram_bot.py` | Yes | Decision/fill notification 與 Telegram webhook processing。 |
| `webhook_dedupe.py` | Yes | 對 `POST /webhook` 與 `POST /tv-webhook` 做短時間 idempotency。 |
| `replay.py` | No | Test/smoke replay CLI only。 |

中文說明：`replay.py` 會 import production modules 來做 fixture replay，但 production service 不會呼叫它。其餘列為 `Yes` 的檔案都在正式 request path 或 execution callback path 中。

## Decision Outcomes（決策結果）

Possible outcomes from `decision_engine.decide_trade()`:

- `CHASE` -> 立即產生 order command
- `RETEST` -> 等待回測進場條件
- `SKIP` -> 不產生任何交易行為

中文說明：大部分 webhook 不會進入交易（`SKIP`）。這通常由以下原因觸發：

- state gate（cooldown / lock）
- insufficient room / weak delta
- high volatility downgrade

## Decision Path Details（Decision 細節）

1. `app.py` 使用 `append_signal_log()` 記錄收到的 webhook。
2. `state_manager.load_state()` 從 `output/state.json` 讀取目前交易狀態。
3. `state_manager.reset_state_if_new_day()` 處理跨日 reset。
4. `state_manager.evaluate_state_gate()` 在 decision engine 前先做第一層風控與交易過濾。
5. 若 gate 通過，`decision_engine.decide_trade()` 評估 signal。
6. 若 `decision` 是 `CHASE` 或 `RETEST`，`command_writer.write_order_command()` 寫入 `output/order_command.json`。
7. command 寫入成功後，`execution_tracker.create_order_record()` 建立 order lifecycle record。
8. `state_manager.apply_decision_effects()` 更新 state，並由 `state_manager.save_state()` 持久化。
9. `command_writer.append_signal_log()` 追加 `decision_result` 到 `output/signal_log.jsonl`。
10. `telegram_bot.notify_decision()` 依環境設定發送或列印 notification。

中文說明：state gate 是實際交易中最重要的過濾層之一。即使策略判斷為 `CHASE`，若 state 顯示 lock / cooldown，最終仍會 `SKIP`。

## Fill and Order Lifecycle（Fill 與 Order Lifecycle）

```text
Broker/execution layer
  -> POST /order-event
  -> execution_tracker.apply_order_event()
  -> command_writer.append_signal_log()
```

中文說明：`POST /order-event` 用來記錄 broker/order lifecycle，例如 ack、reject 或 status update。這條路徑主要更新 execution tracking，不直接產生新的 trading decision。

```text
Broker/execution layer
  -> POST /fill-result
  -> state_manager.is_fill_processed()
  -> state_manager.apply_fill_result()
  -> execution_tracker.apply_fill_to_order()
  -> state_manager.record_fill_processed()
  -> command_writer.append_signal_log()
  -> telegram_bot.notify_fill_result()
```

中文說明：`POST /fill-result` 是 PnL 與 fill 狀態回寫入口。系統會先做 fill dedupe，再更新 trading state、order lifecycle、signal log，最後送出 Telegram fill notification。

## Deployment Dependencies（部署相依）

Production deployment currently assumes:

| Dependency | Current assumption |
|---|---|
| Gunicorn target | `app:app` |
| Working directory | Repo root, same directory as `app.py` and `.env` |
| Config loading | `app.py` calls `load_dotenv()` on root `.env` |
| State storage | File-based `output/` directory |
| Worker count | `--workers 1` strongly recommended because of file-based state |
| nginx upstream | `127.0.0.1:8000` |

中文說明：目前 deployment 與 root layout 高度綁定。若搬動 `app.py` 或 core modules，必須同步檢查 systemd、nginx、scripts、tests 與 import path。

## Notes

- 本文件描述的是現況（as-is）。
- 本文件不是重構設計（not target architecture）。
