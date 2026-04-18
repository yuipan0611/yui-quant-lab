# Strategy spec v1 — Breakout 決策（CHASE / RETEST / SKIP）

**版本**：v1（對應程式 [`decision_engine.py`](../decision_engine.py)）  
**目的**：以可讀規格描述目前「多／空突破 + GEX 價位／bias」規則，便於對照實作、回測與人工檢查。

---

## 1. 輸出與名詞

| 輸出 `decision` | 意義（執行層） |
|-----------------|----------------|
| **CHASE** | 條件允許直接追價；會寫入 `order_command.json`（action = CHASE）。 |
| **RETEST** | 傾向等回測再介入；會寫入 `order_command.json`（action = RETEST）。 |
| **SKIP** | 不產生下單指令（不寫入 CHASE/RETEST 指令檔）。 |

**注意**：HTTP 層另有 `state_manager.evaluate_state_gate`（例如 cooldown）；gate 不通時**不呼叫**本引擎多空分支，直接對外為 `SKIP`（reason 見 gate），該路徑不在本文件逐行展開，但與引擎的 `SKIP` 並存。

### 1.1 Decision trace（決策可解釋層）

每筆由 `decide_trade` 產生的結果會附帶 `trace`（dict），並由 [`app.py`](../app.py) 寫入 `signal_log.jsonl` 的 `decision_result.trace`，供稽核與通知摘要使用。

| 欄位 | 說明 |
|------|------|
| `decision` | 與外層 `decision` 一致。 |
| `reason_code` | 機械可聚合原因碼，例如 `LOW_DELTA`、`BIAS_CONFLICT`、`NO_ROOM`、`EXTENSION_TOO_LARGE`、`CHASE_OK`、`HIGH_VOL_DOWNGRADE`；gate 跳過引擎時為 `STATE_GATE`（由 app 合成）。 |
| `inputs` | `delta_strength`、`room_points`（多：上方 room；空：下方 room；無參考價為 null）、`extension_points`、`regime`、`bias`。 |
| `branch` | `LONG` / `SHORT` / `NONE`。 |
| `downgraded_from` | 僅在 `high_vol` 護欄將 **CHASE → RETEST** 時為 `CHASE`，否則 `null`。 |
| `timestamp` | 決策完成時間（台北時區 ISO8601）。 |

---

## 2. 輸入資料

### 2.1 必填（`trade_input` / webhook payload）

| 欄位 | 型別意義 | 用途 |
|------|-----------|------|
| `symbol` | 字串 | v1 僅預留／日誌，不影響分支。 |
| `signal` | `long_breakout` \| `short_breakout` \| 其他 | 多空方向與 extension 定義；非法值 → `SKIP`。 |
| `price` | 數值 | 現價；與 levels 算「空間」、與 `breakout_level` 算 extension。 |
| `breakout_level` | 數值 | 突破參考價。 |
| `delta_strength` | 數值 | 訊號強度；過弱直接 `SKIP`。 |

### 2.2 GEX／EOD 脈絡（`nq_eod` 或 payload 扁平欄位）

| 欄位 | 說明 |
|------|------|
| `levels` | 價位字典（值可轉 `float` 者才納入）；用於找「最近壓力／支撐」與多空「空間」。 |
| `bias` | 字串（大小寫不敏感）：`bullish` / `bearish` / `neutral` 等；與方向組合決定是否 **bias 不支持 → SKIP**。 |

### 2.3 盤中 regime（`qqq_intraday` 或 payload `regime` / `state.regime`）

- 字串 `regime` 僅附加在 `plan.risk_note`（`qqq_regime=...`），**原則上不參與**多空主分支。
- **例外**：當 `regime` 正規化後等於 **`high_vol`**（見 [`state_manager`](../state_manager.py) 之 `REGIME_HIGH_VOL`）且主流程已得到 **`CHASE`** 時，套用 **§6 高波動護欄**，可能將 `CHASE` **降級為 `RETEST`**。

---

## 3. 固定閾值（程式常數）

| 常數 | 值 | 意義 |
|------|-----|------|
| `MIN_DELTA_STRENGTH` | **0.7** | `delta_strength` 嚴格小於此值 → `SKIP`（理由含數值與閾值）。 |
| `MIN_ROOM_POINTS` | **40** | 多空「往下一個關鍵價」的點數空間不足時 → `RETEST`（見 §5）。 |
| `MAX_EXTENSION_POINTS` | **30** | 突破延伸過大 → `RETEST`。 |

---

## 4. 幾何與 levels 語意

### 4.1 Extension（突破延伸，點數）

- **`long_breakout`**：`extension = price - breakout_level`
- **`short_breakout`**：`extension = breakout_level - price`

### 4.2 最近壓力／支撐

- **最近壓力** `nearest_resistance`：`levels` 中所有 **> price** 的數值取 **最小**；若無 → `None`。
- **最近支撐** `nearest_support`：`levels` 中所有 **< price** 的數值取 **最大**；若無 → `None`。

### 4.3 「空間不足」定義

- **多頭**：若 `nearest_resistance` **存在**，且 `(nearest_resistance - price) < MIN_ROOM_POINTS` → **RETEST**（上方空間不足）。
- **空頭**：若 `nearest_support` **存在**，且 `(price - nearest_support) < MIN_ROOM_POINTS` → **RETEST**（下方空間不足）。
- 若該側最近關鍵價為 **`None`**，該側空間條件**不觸發**（視為空間充足，不因此 RETEST）。

### 4.4 Extension 過大

- 任一合法多空訊號：若 `extension > MAX_EXTENSION_POINTS` → **RETEST**。

---

## 5. 決策順序（引擎內固定順序）

以下為 **`decide_trade`** 內邏輯順序（與程式一致）。

1. **Delta 過弱**  
   - 若 `delta_strength < MIN_DELTA_STRENGTH` → **SKIP**（`delta_strength_below_threshold`）。  
   - 若此時 `signal` 非法，extension 內部以 `0.0` 處理。

2. **Signal 非法**  
   - 若 `signal` 不是 `long_breakout` / `short_breakout` → **SKIP**（`unsupported_signal`）。

3. **計算** `extension`、`nearest_resistance`、`nearest_support`（levels 壞值略過，不拋錯）。

4. **多頭分支**（`long_breakout`）

   | 條件（依序） | 結果 |
   |--------------|------|
   | bias 不支持多頭（見 §5.1 Bias 表） | **SKIP** |
   | 上方空間不足（§4.3） | **RETEST** |
   | extension 過大（§4.4） | **RETEST** |
   | 其餘 | **CHASE** |

5. **空頭分支**（`short_breakout`）

   | 條件（依序） | 結果 |
   |--------------|------|
   | bias 不支持空頭（見 §5.1 Bias 表） | **SKIP** |
   | 下方空間不足（§4.3） | **RETEST** |
   | extension 過大（§4.4） | **RETEST** |
   | 其餘 | **CHASE** |

### 5.1 Bias 與方向對照（對應上表「bias 不支持」）

| `signal` | bias 視為「支持」（不因此 SKIP） |
|----------|----------------------------------|
| `long_breakout` | `bullish`、`neutral` |
| `short_breakout` | `bearish`、`neutral` |

- 其餘或非預期字串 → **保守 SKIP**（reason 標示 bias 不支持該方向）。

---

## 6. 高波動護欄（`high_vol`）

**前提**：正規化後之盤中 `regime` 字串為 **`high_vol`**，且 §5 主流程結果為 **`CHASE`**。

此時再檢查（**任一成立則把 CHASE 改為 RETEST**）：

| 檢查 | 門檻 |
|------|------|
| Delta | `delta_strength < MIN_DELTA_STRENGTH + 0.2`（即 **< 0.9**） |
| Extension | `extension > max(5.0, MAX_EXTENSION_POINTS - 10)`（即 **> 20**） |

- 降級時 `reason` 為 `high_vol_guardrail`，並帶入當下 delta／extension 與門檻數值（供日誌與除錯）。

---

## 7. `plan` 結構（三種 decision 共用骨架）

| `plan` 鍵 | 說明 |
|-----------|------|
| `entry_style` | `CHASE` → `market_chase`；`RETEST` → `wait_retest`；`SKIP` → `no_trade`。 |
| `risk_note` | 人讀說明；若有 regime 字串則附帶 `(qqq_regime=...)`。 |
| `reference_levels` | `price`、`breakout_level`、`nearest_resistance`、`nearest_support`、`extension`。 |

---

## 8. 與 v1 刻意區隔的範圍

- **未**以 `qqq_intraday.regime`（非 `high_vol`）改變 CHASE/RETEST/SKIP 主結果。  
- **未**在引擎內讀取持倉、部位、帳戶或實際成交；狀態與 gate 在 [`app.py`](../app.py) / [`state_manager.py`](../state_manager.py)。  
- 閾值為程式常數；若要「策略參數檔」屬未來版本範圍。

---

## 9. 變更紀錄

| 日期 | 說明 |
|------|------|
| 2026-04-19 | 初版 strategy spec v1，對齊現行 `decide_trade` 實作。 |
