from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DecisionType = Literal["CHASE", "RETEST", "SKIP"]
ExecutionMode = Literal["paper", "json"]


@dataclass(frozen=True)
class DecisionSignal:
    trace_id: str
    event_id: str
    decision: DecisionType
    reason: str
    trace: dict[str, Any]
    plan: dict[str, Any]
    market_payload: dict[str, Any]


@dataclass(frozen=True)
class GateContext:
    trace_id: str
    event_id: str
    payload: dict[str, Any]
    decision_signal: DecisionSignal
    state: dict[str, Any]
    endpoint: str


@dataclass(frozen=True)
class GateResult:
    allow: bool
    reason_code: str
    reason_detail: str
    details: dict[str, Any] = field(default_factory=dict)
    blocked_until: str | None = None


@dataclass(frozen=True)
class RiskedTradeIntent:
    intent_id: str
    trace_id: str
    request_id: str
    symbol: str
    signal: str
    decision: DecisionType
    max_risk: float
    position_size: float
    stop_loss: float | None
    take_profit: float | None
    daily_loss_protection: dict[str, Any]
    risk_reason_codes: list[str]
    decision_plan: dict[str, Any]
    reason: str
    regime: str | None


@dataclass(frozen=True)
class ExecutionCommand:
    command_id: str
    trace_id: str
    intent_id: str
    mode: ExecutionMode
    payload: dict[str, Any]

