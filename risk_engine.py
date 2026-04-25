from __future__ import annotations

import os
from typing import Any

from engine_types import DecisionSignal, RiskedTradeIntent
from reason_codes import REASON_RISK_BASELINE, REASON_RISK_DAILY_LOSS_GUARD, REASON_RISK_HIGH_VOL_TIGHTEN
from state_manager import REGIME_HIGH_VOL


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compute_sl_tp(price: float, decision: str, stop_buffer: float, take_profit_rr: float) -> tuple[float, float]:
    if decision == "CHASE":
        stop_loss = price - stop_buffer
        take_profit = price + (stop_buffer * take_profit_rr)
    else:
        stop_loss = price - (stop_buffer * 0.8)
        take_profit = price + (stop_buffer * take_profit_rr * 0.8)
    return stop_loss, take_profit


def build_trade_intent(
    *,
    request_id: str,
    trace_id: str,
    payload: dict[str, Any],
    decision_signal: DecisionSignal,
    state: dict[str, Any],
) -> RiskedTradeIntent:
    price = _safe_float(payload.get("price"), 0.0)
    symbol = str(payload.get("symbol", "")).strip()
    signal = str(payload.get("signal", "")).strip()
    regime = state.get("regime")
    regime_s = str(regime).strip() if regime is not None else None

    base_risk = _env_float("RISK_ENGINE_BASE_RISK", 1.0)
    base_size = _env_float("RISK_ENGINE_BASE_POSITION_SIZE", 1.0)
    stop_buffer = _env_float("RISK_ENGINE_STOP_BUFFER_POINTS", 15.0)
    take_profit_rr = _env_float("RISK_ENGINE_TAKE_PROFIT_RR", 2.0)
    max_daily_loss = _env_float("RISK_ENGINE_MAX_DAILY_LOSS", -300.0)
    daily_realized = _safe_float(state.get("today_realized_pnl"), 0.0)

    risk_reasons: list[str] = [REASON_RISK_BASELINE]

    risk_multiplier = 1.0
    if regime_s == REGIME_HIGH_VOL:
        risk_multiplier = 0.6
        risk_reasons.append(REASON_RISK_HIGH_VOL_TIGHTEN)

    if daily_realized <= max_daily_loss:
        risk_multiplier = 0.0
        risk_reasons.append(REASON_RISK_DAILY_LOSS_GUARD)

    max_risk = max(0.0, base_risk * risk_multiplier)
    position_size = max(0.0, base_size * risk_multiplier)

    stop_loss, take_profit = _compute_sl_tp(
        price=price,
        decision=decision_signal.decision,
        stop_buffer=stop_buffer,
        take_profit_rr=take_profit_rr,
    )

    return RiskedTradeIntent(
        intent_id=f"intent_{request_id}",
        trace_id=trace_id,
        request_id=request_id,
        symbol=symbol,
        signal=signal,
        decision=decision_signal.decision,
        max_risk=max_risk,
        position_size=position_size,
        stop_loss=stop_loss,
        take_profit=take_profit,
        daily_loss_protection={
            "today_realized_pnl": daily_realized,
            "max_daily_loss": max_daily_loss,
            "blocked": max_risk <= 0.0,
        },
        risk_reason_codes=risk_reasons,
        decision_plan=decision_signal.plan,
        reason=decision_signal.reason,
        regime=regime_s,
    )

