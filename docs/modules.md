# 模組說明

## `decision_engine.py`

- **職責**：依 `TradeInput`、`NqEod`（levels、bias）、`QqqIntraday`（regime 僅附註）輸出 `DecideResult`。  
- **主要 API**：`decide_trade(trade_input, nq_eod=None, qqq_intraday=None, state=None) -> DecideResult`  
- **常數**：`MIN_DELTA_STRENGTH`、`MIN_ROOM_POINTS`、`MAX_EXTENSION_POINTS` 集中於檔案頂部，便於回測掃參。  
- **state-aware 最小行為**：當 `regime=high_vol` 時，對 `CHASE` 增加保守 guardrail，不改變 state gate 的責任邊界。  
- **CLI**：`python decision_engine.py` 印出幾組示範 JSON。

## `state_manager.py`

- **職責**：跨訊號狀態管理（`output/state.json`），包含載入/保存、跨日 reset、state gate、決策後最小更新。  
- **State schema**：`version`、`trading_day`、`today_realized_pnl`（主欄位）、`today_loss`（舊名，與前者同步）、`consecutive_loss`、`cooldown_until`、`regime`、`lock_reason`、`daily_trade_count`、`last_signal_ts`、`last_decision`、`last_decision_reason`、`updated_at`。  
- **Regime enum**：`unknown` / `normal` / `trend` / `range` / `high_vol`（固定字串）。  
- **主要 API**：  
  - `load_state(now=None)`：讀取狀態；若壞檔，會備份 `state.json.corrupt.*` 並 fallback 預設值。  
  - `save_state(state)`：原子寫入。  
  - `reset_state_if_new_day(state, now=None)`：跨日重置日內欄位。  
  - `evaluate_state_gate(state, signal_payload, now=None)`：回傳結構化 gate 結果。  
  - `apply_decision_effects(state, decision_result, now=None)`：決策後最小更新（不碰真實 pnl）。  
  - `apply_fill_result(...)`：由 execution 層回報已實現損益；`pnl==0` 不改 `consecutive_loss`。  
  - `is_fill_processed` / `record_fill_processed`：`output/fill_request_ids.json` 去重（`fill_id` 優先，其次 `request_id`）。  
  - `is_fill_request_id_processed` / `record_fill_request_id`：舊 API 相容包裝。

## `execution_tracker.py`

- **職責**：追蹤單筆 decision/command 的 lifecycle（檔案型），並以 JSONL 追加 execution 事件。  
- **儲存**：`output/orders/<request_id>.json`、`output/execution_events.jsonl`。  
- **Lifecycle schema（v2）**：同一 `request_id` 下使用 `brokers` dict；key 為券商名（例如 `broker_A` / `broker_B`）；未指定券商時預設 `single_broker`。舊版 v1 平面欄位會在載入時自動遷移到 `single_broker`。  
- **主要 API**：  
  - `create_order_record(..., broker=None)`：在 `brokers[broker]` 建立 `created/pending`（若檔已存在則合併同一檔的多券商）。  
  - `apply_order_event(payload)`：必填 `broker`（空字串視為 `single_broker`）；更新該券商桶狀態。  
  - `apply_fill_to_order(..., broker=None, ...)`：優先用 `broker+broker_order_id`；否則掃描全檔比對 `broker_order_id` / `client_order_id`；有 `request_id` 時可再精準路由到正確桶。  
  - `log_execution_event(record)`：寫入 execution JSONL（例如 `fill_unlinked`）。

## `time_utils.py`

- **職責**：統一台北時區（UTC+8）時間處理。  
- **主要 API**：`now_taipei()`、`iso_now_taipei()`、`today_str_taipei()`、`parse_iso_dt(value)`。

## `command_writer.py`

- **職責**：將決策／指令寫成執行層可讀的 JSON，並追加 JSONL 日誌。  
- **路徑常數**：`OUTPUT_DIR`、`ORDER_COMMAND_PATH`、`SIGNAL_LOG_PATH`（預設相對於程序 cwd 的 `output/`）。  
- **主要 API**：  
  - `write_order_command(command, allowed_actions=...)`：覆寫 `order_command.json`，自動補 `timestamp`、`command_id`。  
  - `append_signal_log(record)`：追加 `signal_log.jsonl`。  
  - `read_order_command` / `read_order_command_debug`、`clear_order_command`：讀取與清除。  
  - `extend_allowed_actions(*actions)`：執行期擴充允許的 `action`。  
- **寫入策略**：暫存檔 + `fsync` + `os.replace`，降低讀到半寫入 JSON 的機率。

## `app.py`

- **職責**：Flask 應用；對外 HTTP 介面。  
- **路由**：  
  - `GET /health`：回傳 `{"status":"ok"}`。  
  - `POST /webhook`：解析 JSON、`request_id`、raw log、state gate、decision、寫指令、更新 state、decision log、通知。  
  - `POST /fill-result`：回報已實現 `pnl`；去重（`fill_id` 優先）；建議帶 `broker`；並嘗試更新 lifecycle。  
  - `POST /order-event`：執行端回報訂單事件並更新 lifecycle（必填 `broker`）。  
- **追蹤性**：每筆請求會在 log / command / response / notify 中共用同一 `request_id`。  
- **可觀測性**：decision log 固定包含 `state_snapshot_before` 與 `state_snapshot_after`。
- **本機**：`python app.py`，debug 模式預設開啟；正式環境建議關閉並以前置代理處理 TLS。

## `telegram_bot.py`

- **職責**：決策通知。  
- **主要 API**：`notify_decision(summary)`。  
- **行為**：支援 `ENABLE_TELEGRAM_NOTIFY` 開關；缺少 token/chat_id 時自動 fallback print，不會中斷 webhook 主流程。

## `output/`

執行期產物目錄；版本庫中可保留空目錄說明或以 `.gitkeep` 追蹤，實際 JSON／JSONL 是否納入版控依你的部署慣例決定。
