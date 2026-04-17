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

# --- 閾值（集中管理，方便回測時掃參） ---
MIN_DELTA_STRENGTH = 0.7
MIN_ROOM_POINTS = 40
MAX_EXTENSION_POINTS = 30

Decision = Literal["CHASE", "RETEST", "SKIP"]
Signal = Literal["long_breakout", "short_breakout"]
EntryStyle = Literal["market_chase", "wait_retest", "no_trade"]


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


class DecideResult(TypedDict):
    decision: Decision
    reason: str
    plan: Plan


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


def decide_trade(
    trade_input: TradeInput,
    nq_eod: NqEod,
    qqq_intraday: QqqIntraday,
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
    """
    _ = str(trade_input.get("symbol", ""))  # 預留給日後路由／日誌
    signal_raw = trade_input.get("signal", "")
    price = float(trade_input["price"])
    breakout_level = float(trade_input["breakout_level"])
    delta_strength = float(trade_input["delta_strength"])

    levels = nq_eod.get("levels") or {}
    if not isinstance(levels, dict):
        levels = {}

    bias = nq_eod.get("bias", "")
    if not isinstance(bias, str):
        bias = str(bias)

    regime = qqq_intraday.get("regime")
    regime_s = regime.strip() if isinstance(regime, str) else (str(regime) if regime is not None else "")

    nearest_r = _nearest_resistance_above_price(levels, price)
    nearest_s = _nearest_support_below_price(levels, price)

    def _result(decision: Decision, reason: str, extension: float) -> DecideResult:
        return {
            "decision": decision,
            "reason": reason,
            "plan": _build_plan(
                decision,
                price=price,
                breakout_level=breakout_level,
                nearest_resistance=nearest_r,
                nearest_support=nearest_s,
                extension=extension,
                regime=regime_s or None,
            ),
        }

    # 1) delta 過弱
    if delta_strength < MIN_DELTA_STRENGTH:
        ext = (
            _calculate_extension(signal_raw, price, breakout_level)  # type: ignore[arg-type]
            if signal_raw in ("long_breakout", "short_breakout")
            else 0.0
        )
        return _result(
            "SKIP",
            (
                f"delta_strength_below_threshold "
                f"(value={delta_strength:.4f}, min={MIN_DELTA_STRENGTH})"
            ),
            ext,
        )

    # 2) 非法 signal（reason 固定；不計入多空規則）
    if signal_raw not in ("long_breakout", "short_breakout"):
        return _result("SKIP", "unsupported_signal", 0.0)

    signal: Signal = signal_raw  # type: ignore[assignment]

    extension = _calculate_extension(signal, price, breakout_level)

    if signal == "long_breakout":
        if not _is_bias_supported(signal, bias):
            decision = "SKIP"
            reason = f"bias_not_supporting_long (bias={bias!r})"
        elif nearest_r is not None and (nearest_r - price) < MIN_ROOM_POINTS:
            decision = "RETEST"
            reason = (
                f"room_to_resistance_below_{MIN_ROOM_POINTS} "
                f"(resistance={nearest_r:.4f}, room={nearest_r - price:.4f})"
            )
        elif extension > MAX_EXTENSION_POINTS:
            decision = "RETEST"
            reason = (
                f"extension_above_{MAX_EXTENSION_POINTS} "
                f"(extension={extension:.4f})"
            )
        else:
            decision = "CHASE"
            reason = (
                f"chase_ok (extension={extension:.4f}, "
                f"nearest_resistance={nearest_r}, room_ok=True)"
            )
    else:
        if not _is_bias_supported(signal, bias):
            decision = "SKIP"
            reason = f"bias_not_supporting_short (bias={bias!r})"
        elif nearest_s is not None and (price - nearest_s) < MIN_ROOM_POINTS:
            decision = "RETEST"
            reason = (
                f"room_to_support_below_{MIN_ROOM_POINTS} "
                f"(support={nearest_s:.4f}, room={price - nearest_s:.4f})"
            )
        elif extension > MAX_EXTENSION_POINTS:
            decision = "RETEST"
            reason = (
                f"extension_above_{MAX_EXTENSION_POINTS} "
                f"(extension={extension:.4f})"
            )
        else:
            decision = "CHASE"
            reason = (
                f"chase_ok (extension={extension:.4f}, "
                f"nearest_support={nearest_s}, room_ok=True)"
            )

    return _result(decision, reason, extension)


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
