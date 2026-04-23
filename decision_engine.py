"""
Breakout + GEX context 的輕量決策引擎。

決策順序（由強到弱）：
1. delta_strength 低於閾值 → SKIP
2. signal 非法 → SKIP（reason: unsupported_signal）
3. 計算 extension（依 long / short 定義不同），再分 long / short 分支
4. long_breakout：bias 不支持 → SKIP；上方空間不足 → RETEST；extension 過大 → RETEST；否則 CHASE
5. short_breakout：bias 不支持 → SKIP；下方空間不足 → RETEST；extension 過大 → RETEST；否則 CHASE

qqq_intraday.regime 本版僅供輸出參考（寫入 risk_note），不參與分支。
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from state_manager import REGIME_HIGH_VOL
from time_utils import iso_now_taipei

# --- 閾值（集中管理，方便回測時掃參） ---
MIN_DELTA_STRENGTH = 0.7
MIN_ROOM_POINTS = 40
MAX_EXTENSION_POINTS = 30

Decision = Literal["CHASE", "RETEST", "SKIP"]
Signal = Literal["long_breakout", "short_breakout"]
EntryStyle = Literal["market_chase", "wait_retest", "no_trade"]
Branch = Literal["LONG", "SHORT", "NONE"]

# --- decision trace（可解釋層）reason_code ---
REASON_LOW_DELTA = "LOW_DELTA"
REASON_UNSUPPORTED_SIGNAL = "UNSUPPORTED_SIGNAL"
REASON_BIAS_CONFLICT = "BIAS_CONFLICT"
REASON_NO_ROOM = "NO_ROOM"
REASON_EXTENSION_TOO_LARGE = "EXTENSION_TOO_LARGE"
REASON_CHASE_OK = "CHASE_OK"
REASON_HIGH_VOL_DOWNGRADE = "HIGH_VOL_DOWNGRADE"
# 由 app 層 state gate 合成 trace 時使用（引擎內不會回傳此 code）
REASON_STATE_GATE = "STATE_GATE"


class TradeInput(TypedDict):
    symbol: str
    signal: str
    price: float
    breakout_level: float
    delta_strength: float


class NqEod(TypedDict):
    levels: dict[str, float]
    bias: str


class QqqIntraday(TypedDict):
    regime: str


class ReferenceLevels(TypedDict):
    price: float
    breakout_level: float
    nearest_resistance: float | None
    nearest_support: float | None
    extension: float


class Plan(TypedDict):
    entry_style: EntryStyle
    risk_note: str
    reference_levels: ReferenceLevels


class DecisionTraceInputs(TypedDict):
    """trace.inputs：欄位齊全，無資料處以 null。"""

    delta_strength: float | None
    room_points: float | None
    extension_points: float | None
    regime: str | None
    bias: str | None


class DecisionTrace(TypedDict):
    """單筆決策可追溯結構（dict）。"""

    decision: Decision
    reason_code: str
    inputs: DecisionTraceInputs
    branch: Branch
    downgraded_from: Decision | None
    delta_strength: float
    extension_points: float
    room_points: float | None
    signal: str
    bias: str
    regime: str | None
    thresholds: dict[str, float]
    gates: dict[str, bool]
    # debug convenience only，不是正式 API 契約。
    compact_summary: str
    timestamp: str


class DecideResult(TypedDict):
    decision: Decision
    reason: str
    plan: Plan
    trace: DecisionTrace


def _normalize_signal(signal_raw: Any) -> str:
    """輸入正規化：僅做字串清理，不改變策略語義。"""
    if signal_raw is None:
        return ""
    if isinstance(signal_raw, str):
        return signal_raw.strip().lower()
    return str(signal_raw).strip().lower()


def _display_float(value: float | None, digits: int = 4) -> str:
    """統一顯示格式：None -> N/A，並避免 -0.0。"""
    if value is None:
        return "N/A"
    v = float(value)
    if abs(v) < 1e-12:
        v = 0.0
    return f"{v:.{digits}f}"


def _float_levels(levels: dict[str, Any]) -> list[float]:
    """從 levels dict 抽出可轉成 float 的數值，忽略壞值。"""
    out: list[float] = []
    for v in levels.values():
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _is_bias_supported(signal: Signal, bias: str) -> bool:
    """
    判斷 EOD bias 是否支持當前突破方向。

    預設：bullish 支持多、bearish 支持空、neutral 兩邊皆可；
    非預期字串視為不支持（保守 SKIP）。
    """
    b = bias.strip().lower() if isinstance(bias, str) else ""
    if signal == "long_breakout":
        return b in ("bullish", "neutral")
    if signal == "short_breakout":
        return b in ("bearish", "neutral")
    return False


def _nearest_resistance_above_price(levels: dict[str, Any], price: float) -> float | None:
    """比 price 高的最近壓力：levels 中所有 > price 的最小值；無則 None。"""
    xs = [x for x in _float_levels(levels) if x > price]
    return min(xs) if xs else None


def _nearest_support_below_price(levels: dict[str, Any], price: float) -> float | None:
    """比 price 低的最近支撐：levels 中所有 < price 的最大值；無則 None。"""
    xs = [x for x in _float_levels(levels) if x < price]
    return max(xs) if xs else None


def _calculate_extension(signal: Signal, price: float, breakout_level: float) -> float:
    """依訊號方向計算突破延伸（點數）。"""
    if signal == "long_breakout":
        return float(price) - float(breakout_level)
    return float(breakout_level) - float(price)


def _build_plan(
    decision: Decision,
    *,
    price: float,
    breakout_level: float,
    nearest_resistance: float | None,
    nearest_support: float | None,
    extension: float,
    regime: str | None,
) -> Plan:
    """
    統一組裝 plan；不在此處改寫 decision。

    regime 僅附加於 risk_note（維持 plan 三鍵結構）。
    """
    ref: ReferenceLevels = {
        "price": float(price),
        "breakout_level": float(breakout_level),
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
        "extension": float(extension),
    }

    if decision == "CHASE":
        entry: EntryStyle = "market_chase"
        risk = "條件允許直接追價；注意滑價與假突破。"
    elif decision == "RETEST":
        entry = "wait_retest"
        risk = "空間或延伸不理想，傾向等回測再介入。"
    else:
        entry = "no_trade"
        risk = "條件不成立，觀望。"

    if regime:
        risk = f"{risk} (qqq_regime={regime})"

    return {"entry_style": entry, "risk_note": risk, "reference_levels": ref}


def _branch_from_signal_raw(signal_raw: str) -> Branch:
    if signal_raw == "long_breakout":
        return "LONG"
    if signal_raw == "short_breakout":
        return "SHORT"
    return "NONE"


def _room_points_for_signal(
    signal: Signal, price: float, nearest_r: float | None, nearest_s: float | None
) -> float | None:
    """多：上方 room（resistance - price）；空：下方 room（price - support）；無關鍵價則 null。"""
    if signal == "long_breakout":
        if nearest_r is None:
            return None
        return float(nearest_r) - float(price)
    if nearest_s is None:
        return None
    return float(price) - float(nearest_s)


def _decide_trace(
    *,
    decision: Decision,
    reason_code: str,
    delta_strength: float,
    room_points: float | None,
    extension_points: float,
    signal: str,
    regime: str | None,
    bias: str,
    branch: Branch,
    downgraded_from: Decision | None,
) -> DecisionTrace:
    # trace 僅記錄已存在的決策上下文，不在此重算或發明第二套決策邏輯。
    delta_gate_pass = float(delta_strength) >= float(MIN_DELTA_STRENGTH)
    signal_gate_pass = signal in ("long_breakout", "short_breakout")
    # room_gate_checked=True 代表此次有可檢查的關鍵位（room_points 非空）；
    # 若 room_gate_checked=False，room_gate_pass 固定 False，用 checked 欄位區分「未檢查」與「已檢查但未通過」。
    room_gate_checked = room_points is not None
    room_gate_pass = room_gate_checked and float(room_points) >= float(MIN_ROOM_POINTS)
    # 只在有明確降級證據時才標記 true，避免由最終 decision 反推。
    high_vol_downgraded = downgraded_from is not None
    compact_summary = (
        f"{decision}|{branch}|{reason_code}|"
        f"delta={_display_float(delta_strength)}|"
        f"ext={_display_float(extension_points)}|"
        f"room={_display_float(room_points)}|"
        f"regime={regime if regime is not None else 'N/A'}"
    )
    return {
        "decision": decision,
        "reason_code": reason_code,
        "inputs": {
            "delta_strength": float(delta_strength),
            "room_points": room_points,
            "extension_points": float(extension_points),
            "regime": regime,
            "bias": bias,
        },
        "branch": branch,
        "downgraded_from": downgraded_from,
        "delta_strength": float(delta_strength),
        "extension_points": float(extension_points),
        "room_points": room_points,
        "signal": signal,
        "bias": bias,
        "regime": regime,
        "thresholds": {
            "min_delta_strength": float(MIN_DELTA_STRENGTH),
            "min_room_points": float(MIN_ROOM_POINTS),
            "max_extension_points": float(MAX_EXTENSION_POINTS),
        },
        "gates": {
            "delta_gate_pass": delta_gate_pass,
            "signal_gate_pass": signal_gate_pass,
            "room_gate_checked": room_gate_checked,
            "room_gate_pass": room_gate_pass,
            "high_vol_downgraded": high_vol_downgraded,
        },
        "compact_summary": compact_summary,
        "timestamp": iso_now_taipei(),
    }


def _finish_decide(
    decision: Decision,
    reason: str,
    extension_val: float,
    *,
    reason_code: str,
    branch: Branch,
    signal: str,
    delta_strength: float,
    room_points: float | None,
    regime: str | None,
    bias: str,
    nearest_r: float | None,
    nearest_s: float | None,
    price: float,
    breakout_level: float,
    downgraded_from: Decision | None = None,
) -> DecideResult:
    # 單一出口：集中組裝 decision/reason/plan/trace，確保輸出結構一致。
    return {
        "decision": decision,
        "reason": reason,
        "plan": _build_plan(
            decision,
            price=price,
            breakout_level=breakout_level,
            nearest_resistance=nearest_r,
            nearest_support=nearest_s,
            extension=float(extension_val),
            regime=regime,
        ),
        "trace": _decide_trace(
            decision=decision,
            reason_code=reason_code,
            delta_strength=delta_strength,
            room_points=room_points,
            extension_points=float(extension_val),
            signal=signal,
            regime=regime,
            bias=bias,
            branch=branch,
            downgraded_from=downgraded_from,
        ),
    }


def decide_trade(
    trade_input: TradeInput,
    nq_eod: NqEod | None = None,
    qqq_intraday: QqqIntraday | None = None,
    state: dict[str, Any] | None = None,
) -> DecideResult:
    """
    依 breakout 訊號 + GEX（levels / bias）輸出 CHASE / RETEST / SKIP。

    - levels 缺值或非數字時略過該筆，不拋例外。
    - 無上方壓力或無下方支撐時，該側「空間」視為充足（不觸發 RETEST 的空間條件）。

    Parameters
    ----------
    trade_input :
        必含 symbol, signal, price, breakout_level, delta_strength。
    nq_eod :
        levels（GEX 價位）與 bias。
    qqq_intraday :
        regime 僅供輸出參考。

    Returns
    -------
    DecideResult
        含既有 `decision` / `reason` / `plan`，以及 `trace`（決策可解釋層）。
    """
    _ = str(trade_input.get("symbol", ""))  # 預留給日後路由／日誌
    signal_raw = trade_input.get("signal", "")
    signal_norm = _normalize_signal(signal_raw)
    price = float(trade_input["price"])
    breakout_level = float(trade_input["breakout_level"])
    delta_strength = float(trade_input["delta_strength"])

    nq_eod = nq_eod or {
        "levels": trade_input.get("levels", {}),  # type: ignore[dict-item]
        "bias": trade_input.get("bias", "neutral"),  # type: ignore[dict-item]
    }
    qqq_intraday = qqq_intraday or {
        "regime": trade_input.get("regime", ""),  # type: ignore[dict-item]
    }

    levels = nq_eod.get("levels") or {}
    if not isinstance(levels, dict):
        levels = {}

    bias = nq_eod.get("bias", "")
    if not isinstance(bias, str):
        bias = str(bias)

    regime = qqq_intraday.get("regime")
    if (not regime) and state:
        regime = state.get("regime")
    regime_s = regime.strip() if isinstance(regime, str) else (str(regime) if regime is not None else "")

    nearest_r = _nearest_resistance_above_price(levels, price)
    nearest_s = _nearest_support_below_price(levels, price)
    regime_display = regime_s or None

    # gate 順序刻意先做 delta，再做 signal 驗證：先過濾弱訊號，減少後續無效分支成本。
    # 1) delta 過弱
    if delta_strength < MIN_DELTA_STRENGTH:
        br = _branch_from_signal_raw(signal_norm)
        ext = (
            _calculate_extension(signal_norm, price, breakout_level)  # type: ignore[arg-type]
            if signal_norm in ("long_breakout", "short_breakout")
            else 0.0
        )
        room_pts: float | None = None
        if br == "LONG":
            room_pts = _room_points_for_signal("long_breakout", price, nearest_r, nearest_s)
        elif br == "SHORT":
            room_pts = _room_points_for_signal("short_breakout", price, nearest_r, nearest_s)
        return _finish_decide(
            "SKIP",
            (
                f"delta_strength_below_threshold "
                f"(value={delta_strength:.4f}, min={MIN_DELTA_STRENGTH})"
            ),
            ext,
            reason_code=REASON_LOW_DELTA,
            branch=br,
            signal=signal_norm,
            delta_strength=delta_strength,
            room_points=room_pts,
            regime=regime_display,
            bias=bias,
            nearest_r=nearest_r,
            nearest_s=nearest_s,
            price=price,
            breakout_level=breakout_level,
        )

    # 2) 非法 signal（reason 固定；不計入多空規則）
    if signal_norm not in ("long_breakout", "short_breakout"):
        return _finish_decide(
            "SKIP",
            "unsupported_signal",
            0.0,
            reason_code=REASON_UNSUPPORTED_SIGNAL,
            branch="NONE",
            signal=signal_norm,
            delta_strength=delta_strength,
            room_points=None,
            regime=regime_display,
            bias=bias,
            nearest_r=nearest_r,
            nearest_s=nearest_s,
            price=price,
            breakout_level=breakout_level,
        )

    signal: Signal = signal_norm  # type: ignore[assignment]
    extension = _calculate_extension(signal, price, breakout_level)
    room_pts = _room_points_for_signal(signal, price, nearest_r, nearest_s)

    if signal == "long_breakout":
        if not _is_bias_supported(signal, bias):
            decision = "SKIP"
            # reason_code 給機器穩定判讀；reason 給人讀，允許描述文字調整。
            reason = f"bias_not_supporting_long (bias={bias!r})"
            reason_code = REASON_BIAS_CONFLICT
        # room 只在存在最近壓力位時才評估；若無壓力位，視為不觸發 room 限制。
        elif nearest_r is not None and (nearest_r - price) < MIN_ROOM_POINTS:
            decision = "RETEST"
            reason = (
                f"room_to_resistance_below_{MIN_ROOM_POINTS} "
                f"(resistance={_display_float(nearest_r)}, room={_display_float(nearest_r - price)})"
            )
            reason_code = REASON_NO_ROOM
        elif extension > MAX_EXTENSION_POINTS:
            decision = "RETEST"
            reason = (
                f"extension_above_{MAX_EXTENSION_POINTS} "
                f"(extension={_display_float(extension)})"
            )
            reason_code = REASON_EXTENSION_TOO_LARGE
        else:
            decision = "CHASE"
            reason = (
                f"chase_ok (extension={_display_float(extension)}, "
                f"nearest_resistance={_display_float(nearest_r)}, room_ok=True)"
            )
            reason_code = REASON_CHASE_OK
    else:
        if not _is_bias_supported(signal, bias):
            decision = "SKIP"
            # reason_code 給機器穩定判讀；reason 給人讀，允許描述文字調整。
            reason = f"bias_not_supporting_short (bias={bias!r})"
            reason_code = REASON_BIAS_CONFLICT
        # room 只在存在最近支撐位時才評估；若無支撐位，視為不觸發 room 限制。
        elif nearest_s is not None and (price - nearest_s) < MIN_ROOM_POINTS:
            decision = "RETEST"
            reason = (
                f"room_to_support_below_{MIN_ROOM_POINTS} "
                f"(support={_display_float(nearest_s)}, room={_display_float(price - nearest_s)})"
            )
            reason_code = REASON_NO_ROOM
        elif extension > MAX_EXTENSION_POINTS:
            decision = "RETEST"
            reason = (
                f"extension_above_{MAX_EXTENSION_POINTS} "
                f"(extension={_display_float(extension)})"
            )
            reason_code = REASON_EXTENSION_TOO_LARGE
        else:
            decision = "CHASE"
            reason = (
                f"chase_ok (extension={_display_float(extension)}, "
                f"nearest_support={_display_float(nearest_s)}, room_ok=True)"
            )
            reason_code = REASON_CHASE_OK

    downgraded_from: Decision | None = None
    reason_code_out: str = reason_code
    # 高波動降級 CHASE -> RETEST 屬風控保護，不代表反向訊號成立。
    if regime_s == REGIME_HIGH_VOL and decision == "CHASE":
        high_vol_delta_floor = MIN_DELTA_STRENGTH + 0.2
        high_vol_extension_cap = max(5.0, MAX_EXTENSION_POINTS - 10.0)
        if delta_strength < high_vol_delta_floor or extension > high_vol_extension_cap:
            decision = "RETEST"
            reason = (
                "high_vol_guardrail "
                f"(delta={_display_float(delta_strength)}, min={_display_float(high_vol_delta_floor)}, "
                f"extension={_display_float(extension)}, cap={_display_float(high_vol_extension_cap)})"
            )
            reason_code_out = REASON_HIGH_VOL_DOWNGRADE
            downgraded_from = "CHASE"

    return _finish_decide(
        decision,
        reason,
        extension,
        reason_code=reason_code_out,
        branch="LONG" if signal == "long_breakout" else "SHORT",
        signal=signal,
        delta_strength=delta_strength,
        room_points=room_pts,
        regime=regime_display,
        bias=bias,
        nearest_r=nearest_r,
        nearest_s=nearest_s,
        price=price,
        breakout_level=breakout_level,
        downgraded_from=downgraded_from,
    )


if __name__ == "__main__":
    # 最近上方壓力要夠遠（>= MIN_ROOM_POINTS），否則會變 RETEST
    levels_demo = {"GEX1": 20100.0, "GEX2": 20200.0, "GEX3": 20280.0}

    ex_chase = decide_trade(
        {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20140.0,
            "delta_strength": 0.85,
        },
        {"levels": levels_demo, "bias": "bullish"},
        {"regime": "strong"},
    )

    ex_retest_room = decide_trade(
        {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20175.0,
            "breakout_level": 20140.0,
            "delta_strength": 0.9,
        },
        {"levels": levels_demo, "bias": "bullish"},
        {"regime": "neutral"},
    )

    ex_skip = decide_trade(
        {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20140.0,
            "delta_strength": 0.5,
        },
        {"levels": levels_demo, "bias": "bearish"},
        {"regime": "weak"},
    )

    for label, r in (
        ("CHASE", ex_chase),
        ("RETEST", ex_retest_room),
        ("SKIP", ex_skip),
    ):
        print(f"--- {label} ---")
        print(json.dumps(r, indent=2))
