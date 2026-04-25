# Engine v2 上線前 Smoke Test

本文件提供 `ENGINE_V2_ENABLED` 切換前後的最小上線驗證流程，目標是：
- 不破壞既有 `/tv-webhook` 與 `/webhook` 行為。
- 確認 v2 分層（`decision_engine` / `state_gate` / `risk_engine` / `execution_router`）可正常運作。
- 提供可快速回退（rollback）的操作步驟。

---

## 0) 測試前準備

### 必要條件
- 已可啟動服務（Flask / Gunicorn 任一）。
- `.env` 已設定至少：
  - `TV_WEBHOOK_SECRET`
  - `ENGINE_V2_ENABLED`（測試時會切換）
- 專案根目錄存在 `output/`（若不存在會由程式自動建立）。

### 建議先清理舊輸出（避免判讀混淆）
- 刪除或備份以下檔案/資料夾：
  - `output/order_command.json`
  - `output/signal_log.jsonl`
  - `output/state.json`
  - `output/webhook_dedupe.json`
  - `output/execution_events.jsonl`
  - `output/orders/`

---

## 1) `ENGINE_V2_ENABLED=false` 的 v1 回歸測試步驟

1. 設定 `ENGINE_V2_ENABLED=false`，重啟服務。
2. 呼叫 `/health`，確認服務存活（HTTP 200）。
3. 發送一筆合法 `/tv-webhook`（見下方 curl 範例）。
4. 確認回應：
   - HTTP 200
   - `ok=true`
   - `decision` 為 `CHASE|RETEST|SKIP`
   - `reason_code` 存在
5. 再送同一筆 payload（重複請求）：
   - 應回傳 duplicate 相關結果（例如 `duplicate_ignored` / `DUPLICATE_IGNORED`）
6. 送一筆 `/webhook`（非 tv）合法 payload：
   - 回應 `status=success`
   - `decision` 與 `trace` 存在
7. 驗證 `/fill-result` 基本流程（可選但建議）：
   - 首次 fill `applied=true`
   - 同 fill 再送一次 `applied=false` 且 `reason=duplicate_fill`

---

## 2) `ENGINE_V2_ENABLED=true` 的 v2 測試步驟

1. 設定 `ENGINE_V2_ENABLED=true`，重啟服務。
2. 呼叫 `/health` 確認服務存活。
3. 發送一筆合法 `/tv-webhook`：
   - 確認 HTTP 200、`ok=true`、`decision` 與 `reason_code` 存在。
4. 檢查 `output/order_command.json`（若決策為 `CHASE|RETEST`）：
   - 應存在 `risk` 物件（v2 execution router 寫入）。
   - `risk` 中應可看到 `max_risk`、`position_size`、`risk_reason_codes`。
5. 驗證 gate block 路徑（擇一）：
   - 設定 `STATE_GATE_MAX_LOSS_STREAK=2` 並建立 state 使 `consecutive_loss>=2`，再送 webhook。
   - 預期 `decision=SKIP`，`reason_code` 為 gate 類型（例如 `LOSS_STREAK_BLOCKED`）。
6. 驗證 risk 保護路徑（可選）：
   - 透過 state 讓當日損益低於風險引擎門檻（例如 `today_realized_pnl` 很低）。
   - 預期指令中 `risk.max_risk=0` 或 `position_size=0`（視設定）。
7. 再送一次完全相同 payload：
   - 仍應正確回 duplicate（不因 v2 破壞去重）。

---

## 3) `/tv-webhook` curl 測試範例

> 請把 `<HOST>`、`<SECRET>` 替換成你的實際值。

```bash
curl -X POST "http://<HOST>/tv-webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "<SECRET>",
    "symbol": "MNQ",
    "signal": "long_breakout",
    "price": 20150.0,
    "breakout_level": 20145.0,
    "delta_strength": 0.92,
    "bias": "bullish",
    "levels": {"r1": 20300.0, "s1": 20000.0}
  }'
```

預期回應（欄位示意）：

```json
{
  "ok": true,
  "decision": "CHASE",
  "reason_code": "CHASE_OK",
  "request_id": "20260425T223000_ab12cd",
  "trace": {
    "decision": "CHASE",
    "reason_code": "CHASE_OK"
  }
}
```

---

## 4) 預期 output/state/log 檢查項目

### A. `output/signal_log.jsonl`
- 每次 webhook 應新增紀錄。
- 至少可看到：
  - `event_type=tv_webhook_received`（tv 路徑）
  - `event_type=decision_result`
  - `request_id`
  - `trace.reason_code`

### B. `output/state.json`
- webhook 後應更新：
  - `last_decision`
  - `last_decision_reason`
  - `updated_at`
- fill-result 後應更新：
  - `today_realized_pnl`
  - `consecutive_loss`
  - `cooldown_until`（若有傳 cooldown）

### C. `output/order_command.json`
- 僅當決策為 `CHASE|RETEST` 預期存在。
- v1：核心欄位含 `action/symbol/price/plan/request_id/reason`。
- v2：除上述外，應有 `risk` 區塊（`max_risk/position_size/...`）。

### D. `output/execution_events.jsonl` 與 `output/orders/*.json`
- 若有建立訂單生命週期，應看得到對應 `request_id`。
- `/order-event`、`/fill-result` 後狀態應可被追蹤。

---

## 5) `reason_code` 對照表

### Decision 層
- `LOW_DELTA`：訊號強度不足
- `UNSUPPORTED_SIGNAL`：不支援的 signal
- `BIAS_CONFLICT`：方向與 bias 不一致
- `NO_ROOM`：空間不足（鄰近壓力/支撐）
- `EXTENSION_TOO_LARGE`：延伸過大
- `CHASE_OK`：允許追價
- `HIGH_VOL_DOWNGRADE`：高波動降級（v1 guardrail）
- `STATE_GATE`：state gate 阻擋（v1 fallback trace）

### State Gate 層
- `STATE_GATE_PASSED`：可交易
- `COOLDOWN_ACTIVE`：冷卻中
- `SESSION_CLOSED`：非交易時段或 session 關閉
- `DAILY_LOCKED`：日內鎖定
- `LOSS_STREAK_BLOCKED`：連敗達上限
- `DUPLICATE_IGNORED`：重複訊號被忽略

### Risk 層
- `RISK_BASELINE`：使用基準風控參數
- `RISK_DAILY_LOSS_GUARD`：觸發日虧保護
- `RISK_HIGH_VOL_TIGHTEN`：高波動收斂風險

---

## 6) v1 / v2 回應差異表

| 面向 | v1 (`ENGINE_V2_ENABLED=false`) | v2 (`ENGINE_V2_ENABLED=true`) |
|---|---|---|
| 入口 API | `/tv-webhook`、`/webhook`（相同） | `/tv-webhook`、`/webhook`（相同） |
| `/tv-webhook` 回應契約 | 維持現有 `ok/decision/reason_code/request_id/trace` | 相同（相容） |
| 決策流程 | `state_manager.evaluate_state_gate` + `decision_engine` | `decision_engine` + `state_gate` + `risk_engine` + `execution_router` |
| 風控資訊輸出 | 無獨立 risk 結構 | `order_command.json` 內含 `risk` 區塊 |
| 高波動降級責任 | 主要在 `decision_engine` | 可由 v2 流程交由風控層處理（依當前實作配置） |
| 觀測欄位 | `trace` 為主 | `trace` + `gate/risk` 衍生資訊更完整 |

---

## 7) Rollback 步驟（改回 `ENGINE_V2_ENABLED=false`）

1. 將環境變數改回：
   - `ENGINE_V2_ENABLED=false`
2. 重啟服務（systemd/Gunicorn/容器依部署方式）。
3. 驗證：
   - `/health` 回 200
   - `/tv-webhook` 單筆測試成功
   - `/tv-webhook` 重送測試 duplicate 正常
4. 檢查 `signal_log.jsonl` 最新紀錄，確認仍有 `decision_result` 且無異常 traceback。

---

## 8) 測試通過標準（Go / No-Go）

可上線（Go）條件：
- v1 模式 smoke test 全數通過。
- v2 模式 smoke test 全數通過。
- `/tv-webhook` 契約保持相容（欄位與 HTTP code 不破壞）。
- duplicate 行為在 v1/v2 均一致可用。
- 風控/狀態阻擋路徑可觀測（含 reason_code）。
- 日誌與狀態檔更新正常，無阻斷型錯誤。

不得上線（No-Go）條件：
- `/tv-webhook` 回應欄位不相容或非預期 5xx。
- `ENGINE_V2_ENABLED=false` 時無法回歸既有行為。
- v2 模式下 `order_command.json` 缺失必要欄位（尤其 `risk`）。
- state/log 檔異常（無法寫入、格式壞掉、trace 缺失）。

