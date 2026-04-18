# 開發進度與路線圖

## 已完成

- [x] 決策引擎：`decide_trade`、CHASE／RETEST／SKIP、GEX 空間與延伸規則  
- [x] 指令與日誌寫入：`write_order_command`、`append_signal_log`、原子寫入  
- [x] Flask 最小服務：`/health`、`/webhook` 與 JSON 欄位驗證  
- [x] 專案文件：`README`、`docs/architecture`、`docs/modules`  
- [x] State Manager MVP complete：`state_manager.py`、`output/state.json`、跨日 reset、state gate、before/after state snapshots

## 進行中／下一步（建議優先序）

1. **Execution 回報整合**：接 broker fill / execution report，落地 `apply_fill_result(...)` 更新 `today_loss` / `consecutive_loss`。  
2. **Webhook schema 定版**：與資料來源對齊 `levels`、`bias`、`regime`、state override 欄位。  
3. **安全**：webhook 密鑰或簽章、速率限制、禁止 debug 上線。  
4. **Telegram 正式串接**：由 stub/print 升級為實際 Bot API 發送與重試策略。  
5. **測試擴充**：補 decision_engine、command_writer 細項單元測試與失敗注入測試。  

## 長期可選

- 回測框架與閾值掃参腳本  
- 多商品路由與部位狀態機  
- 與券商 API 的正式執行層（本 repo 刻意保持「決策與檔案介面」輕量）  
