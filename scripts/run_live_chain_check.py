#!/usr/bin/env python3
"""
一鍵驗證：TradingView payload -> HTTP(/health, /tv-webhook) -> Telegram。

執行方式（專案根目錄）:
  python scripts/run_live_chain_check.py

環境變數 WEBHOOK_BASE_URL：預設 http://127.0.0.1（對應 VPS 上 Nginx:80）。
本機只跑 `python app.py`（預設 port 5000）時請設:
  WEBHOOK_BASE_URL=http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(dotenv_path=ROOT / ".env", override=False)

from app import _get_tv_webhook_secret  # noqa: E402
from telegram_bot import notify_decision  # noqa: E402


def _http_get(url: str, *, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = int(getattr(resp, "status", None) or resp.getcode())
        body = resp.read().decode("utf-8", errors="replace")
        return code, body


def _http_post_json(url: str, payload: dict, *, timeout: float = 12.0) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = int(getattr(resp, "status", None) or resp.getcode())
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    return code, parsed


def main() -> int:
    base = os.environ.get("WEBHOOK_BASE_URL", "http://127.0.0.1").rstrip("/")

    print("[1/3] Flask health check")
    try:
        health_code, health_body = _http_get(f"{base}/health")
    except Exception as exc:  # noqa: BLE001
        print(f"health check failed: {exc}", file=sys.stderr)
        print(
            "hint: 先啟動服務（正式: Nginx+Gunicorn 見 docs/vps_runbook.md；"
            "本機 dev: python app.py 並可設 WEBHOOK_BASE_URL=http://127.0.0.1:5000）",
            file=sys.stderr,
        )
        return 2
    print(f"health status={health_code} body={health_body.strip()}")
    if health_code != 200:
        print("health status is not 200", file=sys.stderr)
        return 1

    print("\n[2/3] TradingView -> /tv-webhook")
    tv_payload = {
        "secret": _get_tv_webhook_secret(),
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150,
        "breakout_level": 20145,
        "delta_strength": 0.92,
        "bias": "bullish",
        "levels": {"s1": 20000, "r1": 20300},
    }
    try:
        tv_code, tv_body = _http_post_json(f"{base}/tv-webhook", tv_payload)
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        print(f"tv-webhook failed: status={exc.code} body={err}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"tv-webhook failed: {exc}", file=sys.stderr)
        return 1

    print(f"tv-webhook status={tv_code}")
    print(json.dumps(tv_body, ensure_ascii=False))
    if tv_code != 200 or not isinstance(tv_body, dict) or tv_body.get("ok") is not True:
        print("tv-webhook response is not ok=true", file=sys.stderr)
        return 1

    # 短時去重：同一 payload 在 TTL 內第二次會 duplicate=true，沒有 decision / request_id。
    if tv_body.get("duplicate") is True:
        print("\n[3/3] Telegram notify smoke — SKIPPED（idempotency：重複 payload 已忽略）")
        print(
            "hint: 若要完整跑決策 + Telegram，可刪除 output/webhook_dedupe.json "
            "或等 WEBHOOK_DEDUPE_TTL_SEC 過後再執行本腳本。",
        )
        print("\nPASS: live chain check completed（duplicate 路徑，HTTP 與去重行為正常）。")
        return 0

    print("\n[3/3] Telegram notify smoke (real send if enabled)")
    decision_summary = {
        "request_id": tv_body.get("request_id"),
        "symbol": "MNQ",
        "signal": "long_breakout",
        "decision": tv_body.get("decision"),
        "reason_code": tv_body.get("reason_code"),
        "trace": tv_body.get("trace") if isinstance(tv_body.get("trace"), dict) else {},
        "regime": "unknown",
    }
    tg_result = notify_decision(decision_summary)
    print(json.dumps(tg_result, ensure_ascii=False))
    if tg_result.get("mode") == "telegram" and tg_result.get("ok") is not True:
        print("telegram mode active but send failed", file=sys.stderr)
        return 1

    print("\nPASS: live chain check completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
