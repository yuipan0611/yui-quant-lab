#!/usr/bin/env python3
"""Smoke test: telegram_bot.notify_decision (stdout fallback vs live Telegram)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Repo root: scripts/ -> parent
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def sample_summary() -> dict:
    return {
        "request_id": "20260419T120000_smoke",
        "symbol": "MNQ",
        "signal": "long_breakout",
        "decision": "CHASE",
        "reason_code": "OK_SMOKE",
        "trace": {
            "branch": "LONG",
            "inputs": {
                "delta_strength": 0.91,
                "extension_points": 12.5,
                "regime": "trend",
            },
        },
        "regime": "trend",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke test for decision Telegram notify.")
    p.add_argument(
        "scenario",
        choices=["print-fallback", "telegram"],
        help=(
            "print-fallback: ENABLE_TELEGRAM_NOTIFY=false, message on stdout, mode=disabled. "
            "telegram: ENABLE_TELEGRAM_NOTIFY=true and real sendMessage (needs token + chat id)."
        ),
    )
    args = p.parse_args()

    if args.scenario == "print-fallback":
        os.environ["ENABLE_TELEGRAM_NOTIFY"] = "false"
    else:
        os.environ["ENABLE_TELEGRAM_NOTIFY"] = "true"
        if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
            print(
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for telegram scenario.",
                file=sys.stderr,
            )
            return 2

    from telegram_bot import notify_decision

    result = notify_decision(sample_summary())
    print("--- notify_decision result ---", flush=True)
    print(result, flush=True)

    if args.scenario == "print-fallback":
        if result.get("mode") != "disabled":
            print("Expected mode=disabled for print-fallback.", file=sys.stderr)
            return 1
    else:
        if result.get("mode") != "telegram" or not result.get("ok"):
            print("Expected mode=telegram and ok=True for telegram scenario.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
