# YUI Quant Lab

個人用的量化交易實驗專案：Flask webhook 接 TradingView／外部訊號，經決策引擎寫入 `output/`，可選 Telegram 通知與指令控制。

## 自己看的開發筆記

### 2026-04-25

- **Telegram 內聯主選單**：`/start`、`/help` 會帶主選單；callback 對應狀態、最新訊號／決策／成交、風控、手動鎖定／解鎖、幫助。
- **手動交易鎖**：寫入 `output/manual_trade_lock.json`（`load_manual_trade_lock` / `save_manual_trade_lock`），風控畫面會一併顯示手動鎖狀態。
- **Callback**：`extract_update_fields` 補上 `callback_query_id`；回覆後呼叫 `answerCallbackQuery`，避免按鈕轉圈。
- **從 JSONL 讀最近一筆**：`tail_jsonl_find_last` 搭配各種欄位路徑，顯示最新訊號／決策／成交（邏輯在 `telegram_bot.py` 的 `_format_*`）。
- **營運腳本**（本機 → VPS）：
  - `scripts/sync_tv_webhook_secret_to_vps.ps1` / `.sh`：把專案 `.env` 的 `TV_WEBHOOK_SECRET` 同步到遠端 `.env`（可選重啟 service）。
  - `scripts/verify_tv_webhook.ps1`：本機驗證 TradingView webhook 相關設定用。
  - `scripts/vps_restart_app.sh`：遠端重啟慣用指令參考。
- **非技術每日檢查**：`docs/daily_1min_checklist_non_tech.md`（上線後快速確認 Bot／按鈕）。

架構總覽仍見 `docs/architecture.md`。

## 本機

- Python 依賴：`requirements.txt`
- 環境變數：複製 `.env.example`（若專案有）為 `.env`；**不要**把 `.env` 提交上庫
- 測試：安裝 dev 依賴後執行 `python -m pytest`（專案慣用測試在 `tests/`）

## 上線相關

- 正式環境 secrets 以 VPS 上專案目錄的 `.env` 為單一來源；同步 webhook secret 用上述 `sync_*` 腳本最省事
- 根目錄的 `yui-quant-lab-upload.tgz` 等本地打包檔已列入 `.gitignore`，不進版控

## 授權與責任

此 repo 僅供個人實驗與學習；實盤風險自負。
