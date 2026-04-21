# Phase 3：單筆 TradingView 真實端到端驗證（文件輸出）

依目前 [app.py](../app.py) 的 `/tv-webhook` 與 `adapt_tv_payload` 行為整理。

---

## 1) 可直接貼進 TradingView「Alert message」的最小 JSON 模板

> **醒目提醒（必讀）**  
> **`secret` 必須由你手動替換成 VPS 上 `.env` 裡 `TV_WEBHOOK_SECRET` 的真值**（逐字一致）。TradingView **無法**讀取伺服器上的 `.env`，也不可能自動帶入該檔；若留占位字或貼錯，只會得到 `invalid_secret`，與 webhook 程式是否啟動無關。

**規則**：整段必須是**合法 JSON 物件**（TradingView 會原樣 POST）。

### 1a) 更保守的最小模板（第一次真實 alert 建議先用）

僅含路由與內部 `REQUIRED_FIELDS` 所需欄位；**不含** `levels` / `bias` 等可選欄位。

```json
{
  "secret": "REPLACE_WITH_TV_WEBHOOK_SECRET_FROM_VPS_ENV",
  "symbol": "MNQ",
  "signal": "long_breakout",
  "price": 20150,
  "breakout_level": 20145,
  "delta_strength": 0.88
}
```

### 1b) 較完整模板（與既有決策／trace 較易對齊）

在 1a 基礎上加上 `levels`、`bias`，方便與歷史測試 payload 對照。

```json
{
  "secret": "REPLACE_WITH_TV_WEBHOOK_SECRET_FROM_VPS_ENV",
  "symbol": "MNQ",
  "signal": "long_breakout",
  "price": 20150,
  "breakout_level": 20145,
  "delta_strength": 0.88,
  "levels": { "s1": 20000, "r1": 20300 },
  "bias": "bullish"
}
```

**欄位對照（與程式一致）**

| 欄位 | 是否必填（`/tv-webhook`） | 說明 |
|------|---------------------------|------|
| `secret` | 是 | 與 `_get_tv_webhook_secret()` 比對；錯則 `403` + `invalid_secret` |
| `symbol` | 是 | 須為**非空字串** |
| `signal` / `price` / `breakout_level` | 是 | 缺任一 → `400` + `bad_payload` |
| `delta_strength` | 建議寫（1a 已含） | 省略時內部預設 `1.0`（見 `adapt_tv_payload`） |
| `levels` / `bias` / `regime` 等 | 否 | 有則會轉進內部 payload，影響 `decide_trade` 與 trace |

---

## 2) Webhook URL 與 Alert 設定說明

**Webhook URL（HTTP、port 80）**

```text
http://<你的VPS公網IP>/tv-webhook
```

範例：`http://<VPS_PUBLIC_IP>/tv-webhook`（請替換成你的公網 IP）

**TradingView 端設定要點**

- 在 Alert 視窗勾選 **Webhook URL**，貼上上述 URL（TradingView 只接受 **80/443**；你目前服務監聽 **80**，符合）。
- **Method**：POST（TradingView 預設行為即 POST JSON body）。
- **Alert message**：貼上第 1 節 JSON（建議先 **1a**），並依醒目提醒手動填入 `secret` 真值。
- 觸發方式：用手動可重現的條件（例如「Once Per Bar Close」）做**單筆**驗證即可；不必開長跑。

**伺服器端前提（不重複 Phase 2 全文）**

- `nohup` 已啟動、`ss` 可見 `:80`、`TV_WEBHOOK_SECRET` 已寫入 VPS 的 `.env`（與 Alert JSON 的 `secret` 一致）。

---

## 3) Phase 3「最短」驗收清單（只做單筆真實 alert）

**主要成功判據（請以此為準）**：不要只看 `app.log` 的 `POST /tv-webhook` **200**；須以 **`output/signal_log.jsonl` 內同一 `request_id` 的完整事件鏈**（`tv_webhook_received` → `webhook_received` → `decision_result`），並搭配 TradingView 觸發後回應（或伺服器端觀察）確認 **`/tv-webhook` 回傳 JSON 含 `ok: true`**，三者一致才算端到端成功。`app.log` 的 200 僅為輔助線索。

1. **TradingView 真實 alert 成功送達**  
   - Alert 觸發後，TradingView 端無紅色 webhook 失敗提示（若有，先記下 HTTP code / 重試訊息）。

2. **`app.log` 有收到（輔助）**  
   - VPS：`tail -n 50 app.log`  
   - 應看到對應時間的 `POST /tv-webhook` 與 **200**（若 403/400，跳到第 4 節排錯）。**僅有 200 不足以代表商業流程完整**。

3. **`output/signal_log.jsonl` 有新事件鏈（主要）**  
   - `tail -n 30 output/signal_log.jsonl`  
   - 同一 `request_id` 下，依序出現（或至少可 grep 到）：`tv_webhook_received` → `webhook_received` → `decision_result`。

4. **Telegram 有收到**  
   - 與 `notify_decision` 一致的一則訊息（內容含本次 `request_id` / `symbol` / `decision` 等，依你 bot 格式）。

5. **decision 結果與 payload 一致**  
   - 在 `decision_result` 那行比對：  
     - `raw_payload`（或前面的 `adapted_internal_payload`）內的 `symbol` / `signal` / `price` / `breakout_level` / `delta_strength`（及你帶的 `levels`/`bias`）  
     - 與 `trace.inputs`、`decision`、`reason` 是否合理對應（例如價格延伸、room 等出現在 `reason` 字串中屬正常）。

---

## 4) 排錯清單（針對四類問題）

### A) `secret` 不對

- **HTTP**：`403`，body 常見 `{"ok":false,"error":"invalid_secret"}`。  
- **查**：VPS `.env` 的 `TV_WEBHOOK_SECRET` 與 Alert JSON 的 `secret` 是否**完全一致**（空白、換行、全形符號、複製到多一個引號）。  
- **查**：`env | grep TV_WEBHOOK` 是否在 shell 裡先 export 了別的值（`load_dotenv(..., override=False)` 時 **環境變數優先**）。

### B) `payload` 欄位缺失 / 非 JSON

- **HTTP**：`400`，`bad_payload`。  
- **常見**：缺 `signal` / `price` / `breakout_level` / `symbol`；`symbol` 不是字串或為空。  
- **查**：Alert message 是否被 Pine 字串截斷、是否多餘逗號導致 JSON 壞掉；可用本機 `curl` 同一 body 對照（Phase 2 腳本）確認不是 TradingView 格式問題。

### C) `decision` 為 `SKIP` 或被 downgrade

- **HTTP 仍可能是 200**（webhook 成功 ≠ 交易決策為 CHASE）。  
- **白話**：`SKIP` **不代表** webhook 壞掉或 TradingView 沒送到；代表**系統有收到請求**，但**商業邏輯判斷這筆不交易**（例如 state gate、風控或策略條件不滿足）。  
- **查**：`signal_log.jsonl` 的 `decision_result`：`gate_result` 是否 `allowed: false`（state gate）；若 `allowed: true` 再看 `trace.reason_code` / `reason` 與 `decision`。  
- **查**：`state_snapshot_before` / `after`（同日冷卻、連虧、trade count 等）是否導致 gate 或引擎走 SKIP。  
- **意義**：此屬**商業邏輯結果**，不是連線失敗；若要「強制 CHASE」需改 payload 或狀態（超出本次「不擴範圍」前提，這裡只判讀）。

### D) Telegram 沒收到

- **先分離**：`signal_log.jsonl` 已有 `decision_result` 且 `command_write.ok` 為真 → 後端主流程已跑完，問題多半在通知層。  
- **查**：`.env` 中 `ENABLE_TELEGRAM_NOTIFY=true`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。  
- **查**：`app.log` 是否有 `[warn] notify_decision failed:`。  
- **查**：再用 Phase 2 的 `python scripts/run_telegram_decision_smoke.py telegram` 驗證 bot 仍 200（避免與 TradingView 混為同一問題）。

---

**範圍聲明**：以上不包含長跑監控、nginx/https/domain、WSGI 改造；僅支援單筆真實 alert 的端到端驗證與判讀。
