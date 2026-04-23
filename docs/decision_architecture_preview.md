# 決策架構圖與核心程式

## 決策架構圖（Mermaid）

```mermaid
flowchart TD
  A["輸入: trade_input / nq_eod / qqq_intraday"] --> B["delta_strength 門檻檢查"]
  B -->|低於門檻| S1["SKIP: LOW_DELTA"]
  B -->|通過| C["signal 合法性檢查"]
  C -->|非法 signal| S2["SKIP: UNSUPPORTED_SIGNAL"]
  C -->|合法| D["計算 extension + 最近阻力/支撐 + room_points"]
  D --> E{"bias 是否支援方向?"}
  E -->|否| S3["SKIP: BIAS_CONFLICT"]
  E -->|是| F{"room 不足 或 extension 過大?"}
  F -->|是| R["RETEST"]
  F -->|否| G["CHASE"]
  G --> H{"高波動 guardrail?"}
  H -->|觸發| R2["RETEST: HIGH_VOL_DOWNGRADE"]
  H -->|不觸發| O["輸出 DecideResult (decision/reason/plan/trace)"]
  R --> O
  R2 --> O
  S1 --> O
  S2 --> O
  S3 --> O
```

## 核心程式（現有專案）

### decide_trade 入口

```python
def decide_trade(
    trade_input: TradeInput,
    nq_eod: NqEod | None = None,
    qqq_intraday: QqqIntraday | None = None,
    state: dict[str, Any] | None = None,
) -> DecideResult:
    """
    依 breakout 訊號 + GEX（levels / bias）輸出 CHASE / RETEST / SKIP。
    """
```

### 關鍵規則（摘要）

```python
if delta_strength < MIN_DELTA_STRENGTH:
    return _finish_decide("SKIP", "...", ..., reason_code=REASON_LOW_DELTA, ...)

if signal_raw not in ("long_breakout", "short_breakout"):
    return _finish_decide("SKIP", "unsupported_signal", 0.0, reason_code=REASON_UNSUPPORTED_SIGNAL, ...)

if signal == "long_breakout":
    # bias / room / extension 檢查 -> SKIP / RETEST / CHASE
else:
    # short_breakout 對稱檢查

if regime_s == REGIME_HIGH_VOL and decision == "CHASE":
    # 高波動 guardrail，可能降級為 RETEST
```

## 原始檔案位置

- `decision_engine.py`
- `README.md`
- `docs/architecture.md`
