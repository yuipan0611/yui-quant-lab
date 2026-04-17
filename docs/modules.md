# 模組說明

## `decision_engine.py`

- **職責**：依 `TradeInput`、`NqEod`（levels、bias）、`QqqIntraday`（regime 僅附註）輸出 `DecideResult`。  
- **主要 API**：`decide_trade(trade_input, nq_eod, qqq_intraday) -> DecideResult`  
- **常數**：`MIN_DELTA_STRENGTH`、`MIN_ROOM_POINTS`、`MAX_EXTENSION_POINTS` 集中於檔案頂部，便於回測掃參。  
- **CLI**：`python decision_engine.py` 印出幾組示範 JSON。

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
  - `POST /webhook`：解析 JSON，檢查 `REQUIRED_FIELDS`（symbol、signal、price、breakout_level、delta_strength），成功時回傳 echo；**尚未**呼叫決策引擎或 command writer。  
- **本機**：`python app.py`，debug 模式預設開啟；正式環境建議關閉並以前置代理處理 TLS。

## `telegram_bot.py`

- **職責**：預留的 Telegram 整合（推播最新指令、或指令入口）。  
- **現況**：僅模組占位，實作待 `roadmap` 排程。

## `output/`

執行期產物目錄；版本庫中可保留空目錄說明或以 `.gitkeep` 追蹤，實際 JSON／JSONL 是否納入版控依你的部署慣例決定。
