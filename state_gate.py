from __future__ import annotations

import os
from typing import Any

from engine_types import GateContext, GateResult
from reason_codes import (
    REASON_COOLDOWN_ACTIVE,
    REASON_DAILY_LOCKED,
    REASON_DUPLICATE_SIGNAL,
    REASON_GATE_PASSED,
    REASON_LOSS_STREAK_BLOCKED,
    REASON_SESSION_CLOSED,
)
from state_manager import evaluate_state_gate


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _is_session_open(payload: dict[str, Any]) -> bool:
    session_open = payload.get("session_open")
    if isinstance(session_open, bool):
        return session_open
    # Keep backward compatibility: if caller does not provide session hints, do not block.
    return True


def evaluate_gate(ctx: GateContext) -> GateResult:
    payload = ctx.payload
    state = ctx.state

    if bool(payload.get("_is_duplicate")):
        return GateResult(
            allow=False,
            reason_code=REASON_DUPLICATE_SIGNAL,
            reason_detail="duplicate_signal_ignored",
        )

    if not _is_session_open(payload):
        return GateResult(
            allow=False,
            reason_code=REASON_SESSION_CLOSED,
            reason_detail="session_closed",
        )

    lock_reason = state.get("lock_reason")
    if isinstance(lock_reason, str) and lock_reason.strip() == "daily_locked":
        return GateResult(
            allow=False,
            reason_code=REASON_DAILY_LOCKED,
            reason_detail="daily_locked",
        )

    loss_streak_limit = _env_int("STATE_GATE_MAX_LOSS_STREAK", 0)
    if loss_streak_limit > 0:
        loss_streak = int(state.get("consecutive_loss", 0) or 0)
        if loss_streak >= loss_streak_limit:
            return GateResult(
                allow=False,
                reason_code=REASON_LOSS_STREAK_BLOCKED,
                reason_detail=f"loss_streak={loss_streak} >= max={loss_streak_limit}",
                details={"consecutive_loss": loss_streak, "max_loss_streak": loss_streak_limit},
            )

    legacy_result = evaluate_state_gate(state, payload)
    if not bool(legacy_result.get("allowed")):
        reason = str(legacy_result.get("reason", "state_gate_blocked"))
        if reason == "cooldown_active":
            return GateResult(
                allow=False,
                reason_code=REASON_COOLDOWN_ACTIVE,
                reason_detail=reason,
                details=dict(legacy_result.get("details") or {}),
                blocked_until=(legacy_result.get("details") or {}).get("cooldown_until"),
            )
        return GateResult(
            allow=False,
            reason_code=REASON_DAILY_LOCKED if "lock" in reason else reason.upper(),
            reason_detail=reason,
            details=dict(legacy_result.get("details") or {}),
        )

    return GateResult(
        allow=True,
        reason_code=REASON_GATE_PASSED,
        reason_detail="state_gate_passed",
        details=dict(legacy_result.get("details") or {}),
    )

