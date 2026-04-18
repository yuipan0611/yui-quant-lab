"""
Fixture replay：以固定 JSON 重播 e2e 流程並驗證輸出摘要。

用法：
  python replay.py fixtures/chase_clean.json
  python replay.py fixtures/chase_clean.json --verbose
  python replay.py fixtures/
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import app as app_module
import command_writer
import e2e_full_flow
import state_manager
from telegram_bot import tail_jsonl_find_last

REPO_ROOT = Path(__file__).resolve().parent


def load_fixture(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """回傳 (webhook_body, pre_state)；pre_state 可為 None。"""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"fixture must be a JSON object: {path}")
    if "webhook" in data:
        wh = data["webhook"]
        if not isinstance(wh, dict):
            raise ValueError(f"fixture.webhook must be an object: {path}")
        pre = data.get("pre_state")
        if pre is not None and not isinstance(pre, dict):
            raise ValueError(f"fixture.pre_state must be an object or omitted: {path}")
        return wh, pre if isinstance(pre, dict) else None
    return data, None


def replay_one_fixture(
    fixture_path: Path,
    *,
    verbose: bool = False,
    fill_pnl: float = 12.5,
    fill_cooldown_minutes: int | None = 5,
) -> dict[str, Any]:
    """
    於暫存目錄跑一次 run_e2e_flow，回傳摘要 dict（供 CLI 列印或測試斷言）。
    """
    fixture_path = fixture_path.resolve()
    webhook, pre_state = load_fixture(fixture_path)

    verbose_extra: dict[str, Any] = {}
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        e2e_full_flow.configure_output_dir(base)
        client = app_module.app.test_client()
        res = e2e_full_flow.run_e2e_flow(
            client,
            webhook_payload=webhook,
            pre_state=pre_state,
            include_duplicate_fill=True,
            fill_pnl=fill_pnl,
            fill_cooldown_minutes=fill_cooldown_minutes,
        )

        tr = res.decision_trace if isinstance(res.decision_trace, dict) else {}
        ins = tr.get("inputs") if isinstance(tr.get("inputs"), dict) else {}

        if verbose:
            verbose_extra["trace_full"] = tr
            last_dec = tail_jsonl_find_last(
                command_writer.SIGNAL_LOG_PATH,
                lambda o: o.get("event_type") == "decision_result",
                max_lines=500,
            )
            verbose_extra["last_decision_result"] = last_dec
            if state_manager.STATE_PATH.is_file():
                try:
                    verbose_extra["state_final"] = json.loads(
                        state_manager.STATE_PATH.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    verbose_extra["state_final"] = None

    out: dict[str, Any] = {
        "fixture": fixture_path.name,
        "http_status": res.webhook_status,
        "decision": res.decision,
        "reason": res.webhook_body.get("reason"),
        "reason_code": tr.get("reason_code") if isinstance(tr, dict) else None,
        "branch": tr.get("branch") if isinstance(tr, dict) else None,
        "downgraded_from": tr.get("downgraded_from") if isinstance(tr, dict) else None,
        "order_command_written": bool(res.order_command),
        "fill_first_applied": bool(res.fill_first_body.get("applied")),
        "fill_duplicate_applied": (
            bool(res.fill_dup_body.get("applied")) if isinstance(res.fill_dup_body, dict) else None
        ),
        "notify_decision_count": len(res.decision_notifications),
        "notify_fill_count": len(res.fill_notifications),
        "inputs_delta_strength": ins.get("delta_strength"),
        "inputs_room_points": ins.get("room_points"),
        "inputs_extension_points": ins.get("extension_points"),
        "inputs_regime": ins.get("regime"),
        "inputs_bias": ins.get("bias"),
    }
    if verbose:
        out["_verbose"] = verbose_extra
    return out


def _print_case_row(r: dict[str, Any]) -> None:
    print(f"fixture: {r.get('fixture')}")
    print(f"http_status: {r.get('http_status')}")
    print(f"decision: {r.get('decision')}")
    print(f"reason: {r.get('reason')}")
    print(f"reason_code: {r.get('reason_code')}")
    print(f"branch: {r.get('branch')}")
    print(f"downgraded_from: {r.get('downgraded_from')}")
    print(f"order_command_written: {r.get('order_command_written')}")
    print(f"fill_first_applied: {r.get('fill_first_applied')}")
    print(f"fill_duplicate_applied: {r.get('fill_duplicate_applied')}")
    print(f"notify_decision_count: {r.get('notify_decision_count')}")
    print(f"notify_fill_count: {r.get('notify_fill_count')}")
    print(f"inputs.delta_strength: {r.get('inputs_delta_strength')}")
    print(f"inputs.room_points: {r.get('inputs_room_points')}")
    print(f"inputs.extension_points: {r.get('inputs_extension_points')}")
    print(f"inputs.regime: {r.get('inputs_regime')}")
    print(f"inputs.bias: {r.get('inputs_bias')}")
    vb = r.get("_verbose")
    if isinstance(vb, dict):
        print("\n-- verbose: full trace JSON --")
        print(json.dumps(vb.get("trace_full"), ensure_ascii=False, indent=2))
        print("\n-- verbose: signal_log last decision_result --")
        print(json.dumps(vb.get("last_decision_result"), ensure_ascii=False, indent=2))
        print("\n-- verbose: state.json final --")
        print(json.dumps(vb.get("state_final"), ensure_ascii=False, indent=2))


def _parse_argv(argv: list[str]) -> tuple[list[Path], bool]:
    verbose = False
    paths: list[Path] = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        elif a.strip():
            paths.append(Path(a))
    return paths, verbose


def _collect_fixture_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.glob("*.json") if p.is_file())
    raise FileNotFoundError(str(target))


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8")
            except Exception:
                pass

    paths, verbose = _parse_argv(argv)
    if len(paths) != 1:
        print("Usage: python replay.py <fixture.json|fixtures_dir/> [--verbose]", file=sys.stderr)
        return 2

    target = paths[0]
    if not target.is_absolute():
        target = (REPO_ROOT / target).resolve()

    try:
        files = _collect_fixture_files(target)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not files:
        print("No *.json fixtures found.", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for fp in files:
        print("\n" + "=" * 72)
        r = replay_one_fixture(fp, verbose=verbose)
        results.append(r)
        _print_case_row(r)

    if len(files) > 1:
        print("\n" + "=" * 72)
        print("Summary")
        print("=" * 72)
        print(f"total cases: {len(results)}")
        dec_counts = Counter(str(x.get("decision") or "") for x in results)
        print(f"CHASE count: {dec_counts.get('CHASE', 0)}")
        print(f"RETEST count: {dec_counts.get('RETEST', 0)}")
        print(f"SKIP count: {dec_counts.get('SKIP', 0)}")
        rc_counts = Counter(str(x.get("reason_code") or "UNKNOWN") for x in results)
        print("reason_code counts:")
        for code, n in sorted(rc_counts.items(), key=lambda t: (-t[1], t[0])):
            print(f"  {code}: {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
