# 開發進度與路線圖

## 已完成

- [x] 決策引擎：`decide_trade`、CHASE／RETEST／SKIP、GEX 空間與延伸規則  
- [x] 指令與日誌寫入：`write_order_command`、`append_signal_log`、原子寫入  
- [x] Flask 最小服務：`/health`、`/webhook` 與 JSON 欄位驗證  
- [x] 專案文件：`README`、`docs/architecture`、`docs/modules`  

## 進行中／下一步（建議優先序）

1. **Webhook → 決策 → 寫檔**：在 `app.py` 驗證通過後組裝 `nq_eod` / `qqq_intraday`（來源可为 webhook 擴充欄位或另一設定檔），呼叫 `decide_trade`，再依結果呼叫 `write_order_command` / `append_signal_log`。  
2. **Webhook 擴充欄位**：與資料來源對齊 `levels`、`bias`、`regime` 等，並定版 JSON schema（可放 `docs/` 或 OpenAPI 片段）。  
3. **安全**：webhook 密鑰或簽章、速率限制、禁止 debug 上線。  
4. **`telegram_bot.py`**：讀取 `output/order_command.json` 或訂閱內部事件，實作推播與（可選）確認流程。  
5. **測試**：對 `decision_engine`、`command_writer` 補單元測試；對 `/webhook` 做整合測試。  

## 長期可選

- 回測框架與閾值掃参腳本  
- 多商品路由與部位狀態機  
- 與券商 API 的正式執行層（本 repo 刻意保持「決策與檔案介面」輕量）  
