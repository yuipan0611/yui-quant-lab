"""
端到端流程：webhook -> decision -> order_command -> fill-result -> state -> Telegram 通知（mock／列印）。

供人工執行：`python e2e_full_flow.py`
供測試匯入：`from e2e_full_flow import configure_output_dir, run_e2e_flow`
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import app as app_module
import command_writer
import execution_tracker
import state_manager
from telegram_bot import tail_jsonl_find_last


def chase_friendly_webhook_payload() -> dict[str, Any]:
    """在目前 v1 規則下傾向產生 CHASE／RETEST（會寫 order_command）的範例 payload。"""
    return {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.92,
        "bias": "bullish",
        "levels": {"r1": 20300.0, "s1": 20000.0},
    }


def configure_output_dir(output_dir: Path) -> None:
    """將檔案型輸出導向指定目錄（需在目錄存在或可建立時呼叫）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    command_writer.OUTPUT_DIR = output_dir
    command_writer.ORDER_COMMAND_PATH = output_dir / "order_command.json"
    command_writer.ORDER_COMMAND_TMP_PATH = output_dir / "order_command.json.tmp"
    command_writer.SIGNAL_LOG_PATH = output_dir / "signal_log.jsonl"

    state_manager.OUTPUT_DIR = output_dir
    state_manager.STATE_PATH = output_dir / "state.json"
    state_manager.FILL_DEDUPE_PATH = output_dir / "fill_request_ids.json"

    execution_tracker.OUTPUT_DIR = output_dir
    execution_tracker.EXECUTION_EVENTS_PATH = output_dir / "execution_events.jsonl"
    execution_tracker.ORDERS_DIR = output_dir / "orders"


@dataclass
class E2EFlowResult:
    webhook_status: int
    webhook_body: dict[str, Any]
    decision: str
    request_id: str | None
    order_command: dict[str, Any] | None
    fill_first_status: int
    fill_first_body: dict[str, Any]
    fill_dup_status: int | None
    fill_dup_body: dict[str, Any] | None
    state_after_first_fill: dict[str, Any]
    decision_trace: dict[str, Any] | None = None
    decision_notifications: list[dict[str, Any]] = field(default_factory=list)
    fill_notifications: list[dict[str, Any]] = field(default_factory=list)


def run_e2e_flow(
    client: Any,
    *,
    webhook_payload: dict[str, Any] | None = None,
    pre_state: dict[str, Any] | None = None,
    include_duplicate_fill: bool = True,
    fill_pnl: float = 12.5,
    fill_cooldown_minutes: int | None = 5,
) -> E2EFlowResult:
    """
    使用已設定之 output 目錄與 Flask test_client，跑完整鏈並收集 Telegram notify 的 summary dict。
    會暫時替換 app_module 上的 notify_decision／notify_fill_result，結束後還原。

    webhook_payload :
        若提供則作為 POST /webhook 的 JSON；否則使用 chase_friendly_webhook_payload()。
    pre_state :
        若提供則在 webhook 前以淺層合併覆寫 `_default_state()` 後寫入 state（例如 cooldown gate）。
    """
    decision_notes: list[dict[str, Any]] = []
    fill_notes: list[dict[str, Any]] = []

    orig_decision = app_module.notify_decision
    orig_fill = app_module.notify_fill_result

    def _capture_decision(s: dict[str, Any]) -> dict[str, Any]:
        decision_notes.append(dict(s))
        return {"ok": True, "status_code": None, "error": None, "mode": "captured"}

    def _capture_fill(s: dict[str, Any]) -> dict[str, Any]:
        fill_notes.append(dict(s))
        return {"ok": True, "status_code": None, "error": None, "mode": "captured"}

    app_module.notify_decision = _capture_decision
    app_module.notify_fill_result = _capture_fill

    try:
        st0 = state_manager._default_state()
        if pre_state:
            for k, v in pre_state.items():
                st0[k] = v
        state_manager.save_state(st0)

        body = webhook_payload if webhook_payload is not None else chase_friendly_webhook_payload()
        wh = client.post("/webhook", json=body)
        wh_body = wh.get_json(silent=True) or {}
        decision = str(wh_body.get("decision", ""))
        request_id = wh_body.get("request_id")
        request_id_s = str(request_id) if request_id else ""
        dec_trace = wh_body.get("trace")
        decision_trace = dec_trace if isinstance(dec_trace, dict) else None

        order_cmd: dict[str, Any] | None = None
        if command_writer.ORDER_COMMAND_PATH.is_file():
            order_cmd = json.loads(command_writer.ORDER_COMMAND_PATH.read_text(encoding="utf-8"))

        fill_body: dict[str, Any] = {
            "request_id": request_id_s,
            "pnl": fill_pnl,
            "fill_id": "e2e_demo_fill_1",
        }
        if fill_cooldown_minutes is not None:
            fill_body["cooldown_minutes"] = fill_cooldown_minutes

        f1 = client.post("/fill-result", json=fill_body)
        f1_body = f1.get_json(silent=True) or {}
        st = state_manager.reset_state_if_new_day(state_manager.load_state())

        f2_status: int | None = None
        f2_body: dict[str, Any] | None = None
        if include_duplicate_fill:
            f2 = client.post("/fill-result", json=fill_body)
            f2_status = f2.status_code
            f2_body = f2.get_json(silent=True) or {}

        return E2EFlowResult(
            webhook_status=wh.status_code,
            webhook_body=wh_body,
            decision=decision,
            request_id=request_id_s or None,
            order_command=order_cmd,
            fill_first_status=f1.status_code,
            fill_first_body=f1_body,
            fill_dup_status=f2_status,
            fill_dup_body=f2_body,
            state_after_first_fill=st,
            decision_trace=decision_trace,
            decision_notifications=decision_notes,
            fill_notifications=fill_notes,
        )
    finally:
        app_module.notify_decision = orig_decision
        app_module.notify_fill_result = orig_fill


def _print_section(title: str) -> None:
    bar = "=" * min(72, max(len(title) + 4, 8))
    print(f"\n{bar}\n{title}\n{bar}")


def main_cli(argv: list[str] | None = None) -> int:
    """CLI：於暫存目錄跑一輪並列印摘要。"""
    _ = argv
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8")
            except Exception:
                pass
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        configure_output_dir(base)

        client = app_module.app.test_client()
        _print_section(
            "E2E demo: webhook -> decision -> order_command -> fill-result -> state -> Telegram notifies (captured)"
        )

        res = run_e2e_flow(client, include_duplicate_fill=True)

        print(f"\n[1] POST /webhook -> HTTP {res.webhook_status}")
        print(json.dumps({"decision": res.decision, "request_id": res.request_id}, ensure_ascii=False, indent=2))

        print("\n[1b] decision trace (HTTP `trace` + signal_log last decision_result)")
        print(json.dumps(res.decision_trace or {}, ensure_ascii=False, indent=2))
        last_dec = tail_jsonl_find_last(
            command_writer.SIGNAL_LOG_PATH,
            lambda o: o.get("event_type") == "decision_result",
            max_lines=500,
        )
        if isinstance(last_dec, dict):
            print(json.dumps({"trace": last_dec.get("trace")}, ensure_ascii=False, indent=2))
        else:
            print("{}")

        print("\n[2] order_command.json (if CHASE/RETEST wrote file)")
        if res.order_command:
            print(json.dumps(res.order_command, ensure_ascii=False, indent=2)[:2000])
        else:
            print("(無檔案 — 決策為 SKIP 或未寫指令)")

        print(f"\n[3] POST /fill-result (first) -> HTTP {res.fill_first_status}")
        print(json.dumps(res.fill_first_body, ensure_ascii=False, indent=2))

        print("\n[4] state.json (after first fill, subset)")
        print(
            json.dumps(
                {
                    "trading_day": res.state_after_first_fill.get("trading_day"),
                    "today_realized_pnl": res.state_after_first_fill.get("today_realized_pnl"),
                    "consecutive_loss": res.state_after_first_fill.get("consecutive_loss"),
                    "cooldown_until": res.state_after_first_fill.get("cooldown_until"),
                    "last_decision": res.state_after_first_fill.get("last_decision"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        print(f"\n[5] POST /fill-result (duplicate resend) -> HTTP {res.fill_dup_status}")
        print(json.dumps(res.fill_dup_body or {}, ensure_ascii=False, indent=2))

        print("\n[6] notify_decision captured (count=%d)" % len(res.decision_notifications))
        for i, n in enumerate(res.decision_notifications, 1):
            print(f"  --- #{i} ---")
            print(
                f"    decision={n.get('decision')} reason_code={n.get('reason_code')} "
                f"request_id={n.get('request_id')}"
            )

        print("\n[7] notify_fill_result captured (count=%d)" % len(res.fill_notifications))
        for i, n in enumerate(res.fill_notifications, 1):
            print(f"  --- #{i} ---")
            print(
                f"    applied={n.get('applied')} dedupe={n.get('dedupe')} "
                f"reason={n.get('reason')} request_id={n.get('request_id')}"
            )

        print(
            "\nTemp output dir (deleted when process exits):\n  "
            + str(base)
            + "\nCopy this folder during the run if you need to inspect files."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
