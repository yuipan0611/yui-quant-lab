from __future__ import annotations

import os
from typing import Any

from command_writer import write_order_command
from engine_types import ExecutionCommand, RiskedTradeIntent


def _execution_mode() -> str:
    raw = str(os.environ.get("EXECUTION_MODE", "paper")).strip().lower()
    if raw in ("paper", "json"):
        return raw
    return "paper"


def build_execution_command(intent: RiskedTradeIntent) -> ExecutionCommand:
    payload: dict[str, Any] = {
        "action": intent.decision,
        "signal": intent.signal,
        "symbol": intent.symbol,
        "price": intent.decision_plan.get("reference_levels", {}).get("price", 0.0),
        "plan": intent.decision_plan,
        "request_id": intent.request_id,
        "reason": intent.reason,
        "regime": intent.regime,
        "risk": {
            "max_risk": intent.max_risk,
            "position_size": intent.position_size,
            "stop_loss": intent.stop_loss,
            "take_profit": intent.take_profit,
            "daily_loss_protection": intent.daily_loss_protection,
            "risk_reason_codes": intent.risk_reason_codes,
        },
    }
    mode = _execution_mode()
    return ExecutionCommand(
        command_id=f"cmd_{intent.request_id}",
        trace_id=intent.trace_id,
        intent_id=intent.intent_id,
        mode=mode,
        payload=payload,
    )


def route_execution(command: ExecutionCommand) -> dict[str, Any]:
    if command.mode not in ("paper", "json"):
        raise ValueError(f"unsupported execution mode={command.mode!r}")
    written = write_order_command(command.payload)
    return {"ok": True, "mode": command.mode, "written_command": written}

