from __future__ import annotations

from copy import deepcopy
import os
import traceback
from uuid import uuid4

from flask import Flask, jsonify, request

from command_writer import append_signal_log, write_order_command
from decision_engine import REASON_STATE_GATE, decide_trade
from execution_tracker import (
    apply_fill_to_order,
    apply_order_event,
    create_order_record,
    log_execution_event,
)
from state_manager import (
    apply_decision_effects,
    apply_fill_result,
    evaluate_state_gate,
    is_fill_processed,
    load_state,
    record_fill_processed,
    reset_state_if_new_day,
    save_state,
)
from telegram_bot import notify_decision, notify_fill_result, process_telegram_webhook
from time_utils import iso_now_taipei

app = Flask(__name__)

# TradingView alert 必填欄位
REQUIRED_FIELDS = [
    "symbol",
    "signal",
    "price",
    "breakout_level",
    "delta_strength",
]


def _new_request_id() -> str:
    ts = iso_now_taipei()[:19].replace("-", "").replace(":", "")
    return f"{ts}_{uuid4().hex[:6]}"


def _json_error(message: str, status_code: int = 400, **extra: object):
    payload = {"status": "error", "message": message}
    payload.update(extra)
    return jsonify(payload), status_code


def _safe_log(record: dict) -> None:
    try:
        append_signal_log(record)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] append_signal_log failed: {exc}")


_TV_SECRET_DEFAULT_WARNED = False


def _get_tv_webhook_secret() -> str:
    """讀取 TV_WEBHOOK_SECRET；未設定或空字串時使用 dev-secret 並於首次提示（不拋錯）。"""
    global _TV_SECRET_DEFAULT_WARNED
    raw = os.environ.get("TV_WEBHOOK_SECRET")
    if raw is None or str(raw).strip() == "":
        if not _TV_SECRET_DEFAULT_WARNED:
            print("[warn] TV_WEBHOOK_SECRET unset or empty; using default dev-secret")
            _TV_SECRET_DEFAULT_WARNED = True
        return "dev-secret"
    return str(raw).strip()


def _sanitize_tv_payload_for_log(tv_json: dict) -> dict:
    """移除 secret，供 signal log 留存（不比對、不寫入內部 payload）。"""
    return {k: v for k, v in tv_json.items() if k != "secret"}


def adapt_tv_payload(sanitized_tv: dict) -> dict:
    """
    TradingView（已移除 secret）→ 內部 /webhook 可吃的 payload。
    不信任外部 request_id（由 route 另行產生）。
    """
    out: dict = {
        "signal": sanitized_tv["signal"],
        "price": sanitized_tv["price"],
        "breakout_level": sanitized_tv["breakout_level"],
        "symbol": str(sanitized_tv["symbol"]).strip(),
        "delta_strength": sanitized_tv.get("delta_strength", 1.0),
    }
    if "timeframe" in sanitized_tv:
        out["timeframe"] = sanitized_tv["timeframe"]
    for key in ("nq_eod", "qqq_intraday", "levels", "bias", "regime", "broker", "client_order_id"):
        if key in sanitized_tv:
            out[key] = sanitized_tv[key]
    return out


def process_webhook_payload(payload: dict, request_id: str) -> dict:
    """
    內部 webhook 核心流程（/webhook 與 /tv-webhook 共用）。
    呼叫端須保證 payload 為 dict 且含 REQUIRED_FIELDS。
    """
    now_iso = iso_now_taipei()

    _safe_log(
        {
            "event_type": "webhook_received",
            "request_id": request_id,
            "timestamp": now_iso,
            "raw_payload": payload,
        }
    )

    state = reset_state_if_new_day(load_state())
    state_snapshot_before = deepcopy(state)
    gate_result = evaluate_state_gate(state, payload)

    if gate_result.get("allowed"):
        nq_eod = payload.get("nq_eod") or {
            "levels": payload.get("levels", {}),
            "bias": payload.get("bias", "neutral"),
        }
        qqq_intraday = payload.get("qqq_intraday") or {
            "regime": payload.get("regime", state.get("regime", "unknown")),
        }
        decision_result = decide_trade(
            payload,
            nq_eod=nq_eod,
            qqq_intraday=qqq_intraday,
            state=state,
        )
    else:
        bias_gate = payload.get("bias", "neutral")
        if not isinstance(bias_gate, str):
            bias_gate = str(bias_gate)
        regime_gate = state.get("regime")
        regime_gate_s = str(regime_gate).strip() if regime_gate is not None else None
        decision_result = {
            "decision": "SKIP",
            "reason": str(gate_result.get("reason", "state_gate_denied")),
            "plan": {
                "entry_style": "no_trade",
                "risk_note": "state gate denied",
                "reference_levels": {
                    "price": float(payload["price"]),
                    "breakout_level": float(payload["breakout_level"]),
                    "nearest_resistance": None,
                    "nearest_support": None,
                    "extension": 0.0,
                },
            },
            "trace": {
                "decision": "SKIP",
                "reason_code": REASON_STATE_GATE,
                "inputs": {
                    "delta_strength": float(payload.get("delta_strength", 0) or 0),
                    "room_points": None,
                    "extension_points": None,
                    "regime": regime_gate_s or None,
                    "bias": bias_gate,
                },
                "branch": "NONE",
                "downgraded_from": None,
                "timestamp": now_iso,
            },
        }

    command_write = {"ok": False, "error": None}
    if decision_result["decision"] in ("CHASE", "RETEST"):
        command = {
            "action": decision_result["decision"],
            "signal": payload["signal"],
            "symbol": payload["symbol"],
            "price": payload["price"],
            "plan": decision_result["plan"],
            "request_id": request_id,
            "reason": decision_result["reason"],
            "regime": state.get("regime"),
            "state_version": state.get("version"),
        }
        try:
            write_order_command(command)
            command_write["ok"] = True
            try:
                create_order_record(
                    request_id=request_id,
                    symbol=str(payload["symbol"]),
                    decision=str(decision_result["decision"]),
                    broker=str(payload.get("broker") or "").strip() or None,
                    client_order_id=str(payload.get("client_order_id") or "").strip() or None,
                    now_iso=now_iso,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] create_order_record failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            command_write["error"] = str(exc)
            print(f"[error] write_order_command failed: {exc}")
            traceback.print_exc()

    state_after = apply_decision_effects(state, decision_result)
    state_save_error = None
    try:
        save_state(state_after)
    except Exception as exc:  # noqa: BLE001
        state_save_error = str(exc)
        print(f"[error] save_state failed: {exc}")

    decision_log = {
        "event_type": "decision_result",
        "request_id": request_id,
        "timestamp": now_iso,
        "raw_payload": payload,
        "gate_result": gate_result,
        "decision": decision_result["decision"],
        "reason": decision_result["reason"],
        "trace": decision_result.get("trace"),
        "command_write": command_write,
        "state_save_error": state_save_error,
        "state_snapshot_before": state_snapshot_before,
        "state_snapshot_after": state_after,
    }
    _safe_log(decision_log)

    try:
        tr_raw = decision_result.get("trace")
        tr = tr_raw if isinstance(tr_raw, dict) else {}
        ins_raw = tr.get("inputs")
        ins = ins_raw if isinstance(ins_raw, dict) else {}
        notify_decision(
            {
                "request_id": request_id,
                "symbol": payload.get("symbol"),
                "signal": payload.get("signal"),
                "decision": decision_result["decision"],
                "reason": decision_result["reason"],
                "regime": state_after.get("regime"),
                "cooldown_until": state_after.get("cooldown_until"),
                "today_realized_pnl": state_after.get("today_realized_pnl"),
                "today_loss": state_after.get("today_loss"),
                "consecutive_loss": state_after.get("consecutive_loss"),
                "trace": tr,
                "reason_code": tr.get("reason_code") or "UNKNOWN",
                "trace_delta_strength": ins.get("delta_strength"),
                "trace_room_points": ins.get("room_points"),
                "trace_extension_points": ins.get("extension_points"),
                "trace_bias": ins.get("bias"),
                "trace_regime": ins.get("regime"),
                "trace_branch": tr.get("branch"),
                "trace_downgraded_from": tr.get("downgraded_from"),
                "trace_timestamp": tr.get("timestamp"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] notify_decision failed: {exc}")

    return {
        "request_id": request_id,
        "decision": decision_result["decision"],
        "reason": decision_result["reason"],
        "trace": decision_result.get("trace"),
        "gate_result": gate_result,
        "command_write": command_write,
        "state_save_error": state_save_error,
    }


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    body = request.get_json(silent=True)
    payload, status = process_telegram_webhook(body, request.headers)
    return jsonify(payload), status


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True)

    if payload is None:
        return _json_error(
            "Invalid or missing JSON body. Please send valid JSON with Content-Type: application/json.",
        )

    if not isinstance(payload, dict):
        return _json_error("JSON payload must be an object.")

    missing_fields = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing_fields:
        return _json_error(
            "Missing required fields.",
            missing_fields=missing_fields,
            required_fields=REQUIRED_FIELDS,
        )

    request_id = _new_request_id()
    result = process_webhook_payload(payload, request_id=request_id)
    return jsonify(
        {
            "status": "success",
            "request_id": result["request_id"],
            "decision": result["decision"],
            "reason": result["reason"],
            "trace": result.get("trace"),
            "gate_result": result["gate_result"],
            "command_write": result["command_write"],
            "state_save_error": result["state_save_error"],
        }
    ), 200


def _tv_bad(msg: str = "bad_payload"):
    return jsonify({"ok": False, "error": msg}), 400


@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return _tv_bad("bad_payload")

    if body.get("secret") != _get_tv_webhook_secret():
        return jsonify({"ok": False, "error": "invalid_secret"}), 403

    for key in ("signal", "price", "breakout_level"):
        if key not in body:
            return _tv_bad("bad_payload")

    if "symbol" not in body:
        return _tv_bad("bad_payload")
    sym_raw = body["symbol"]
    if not isinstance(sym_raw, str) or not sym_raw.strip():
        return _tv_bad("bad_payload")

    request_id = _new_request_id()
    now_iso = iso_now_taipei()
    sanitized = _sanitize_tv_payload_for_log(body)
    internal = adapt_tv_payload(sanitized)

    _safe_log(
        {
            "event_type": "tv_webhook_received",
            "request_id": request_id,
            "timestamp": now_iso,
            "tv_payload_sanitized": sanitized,
            "adapted_internal_payload": internal,
        }
    )

    try:
        result = process_webhook_payload(internal, request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] tv_webhook process_webhook_payload failed: {exc}")
        traceback.print_exc()
        return jsonify({"ok": False, "error": "internal_error"}), 500

    tr_raw = result.get("trace")
    tr = tr_raw if isinstance(tr_raw, dict) else {}
    reason_code = tr.get("reason_code") if isinstance(tr, dict) else None
    if not isinstance(reason_code, str) or not reason_code:
        reason_code = "UNKNOWN"

    return jsonify(
        {
            "ok": True,
            "decision": result["decision"],
            "reason_code": reason_code,
            "request_id": result["request_id"],
            "trace": result.get("trace"),
        }
    ), 200


@app.route("/fill-result", methods=["POST"])
def fill_result():
    payload = request.get_json(silent=True)
    if payload is None:
        return _json_error(
            "Invalid or missing JSON body. Please send valid JSON with Content-Type: application/json.",
        )
    if not isinstance(payload, dict):
        return _json_error("JSON payload must be an object.")

    required = ["pnl"]
    missing = [f for f in required if f not in payload]
    if missing:
        return _json_error("Missing required fields.", missing_fields=missing, required_fields=required)

    req_id = str(payload.get("request_id", "")).strip()
    fill_id = str(payload.get("fill_id", "")).strip() or None
    broker = str(payload.get("broker", "")).strip() or None
    broker_order_id = str(payload.get("broker_order_id", "")).strip() or None
    client_order_id = str(payload.get("client_order_id", "")).strip() or None
    if not req_id and not broker_order_id and not client_order_id:
        return _json_error(
            "Must provide at least one of: request_id, broker_order_id, client_order_id",
            required_any_of=["request_id", "broker_order_id", "client_order_id"],
        )

    if is_fill_processed(fill_id=fill_id, request_id=req_id or None):
        state = reset_state_if_new_day(load_state())
        _safe_log(
            {
                "event_type": "fill_result_duplicate",
                "request_id": req_id or None,
                "fill_id": fill_id,
                "broker": broker,
                "broker_order_id": broker_order_id,
                "client_order_id": client_order_id,
                "timestamp": iso_now_taipei(),
                "fill_payload": payload,
                "dedupe": True,
            }
        )
        try:
            notify_fill_result(
                {
                    "applied": False,
                    "reason": "duplicate_fill",
                    "dedupe": True,
                    "request_id": req_id or None,
                    "fill_id": fill_id,
                    "broker": broker,
                    "broker_order_id": broker_order_id,
                    "client_order_id": client_order_id,
                    "pnl": payload.get("pnl"),
                    "state_save_error": None,
                    "lifecycle": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] notify_fill_result (duplicate) failed: {exc}")
        return jsonify(
            {
                "status": "success",
                "request_id": req_id or None,
                "fill_id": fill_id,
                "broker": broker,
                "applied": False,
                "reason": "duplicate_fill",
                "state_save_error": None,
                "state": state,
            }
        ), 200

    state = reset_state_if_new_day(load_state())
    before = deepcopy(state)
    save_error = None
    lifecycle = None
    try:
        after = apply_fill_result(state, payload)
        save_state(after)
        filled_qty = payload.get("filled_qty")
        avg_fill_price = payload.get("avg_fill_price")
        lifecycle = apply_fill_to_order(
            request_id=req_id or None,
            broker=broker,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            fill_id=fill_id,
            pnl=payload.get("pnl"),
            filled_qty=float(filled_qty) if filled_qty is not None else None,
            avg_fill_price=float(avg_fill_price) if avg_fill_price is not None else None,
            now_iso=iso_now_taipei(),
        )
        if lifecycle is None:
            log_execution_event(
                {
                    "event_type": "fill_unlinked",
                    "request_id": req_id or None,
                    "fill_id": fill_id,
                    "broker": broker,
                    "broker_order_id": broker_order_id,
                    "client_order_id": client_order_id,
                    "fill_payload": payload,
                }
            )
        record_fill_processed(fill_id=fill_id, request_id=req_id or None)
    except Exception as exc:  # noqa: BLE001
        after = state
        save_error = str(exc)
        print(f"[error] apply/save fill result failed: {exc}")

    _safe_log(
        {
            "event_type": "fill_result",
            "request_id": payload.get("request_id"),
            "fill_id": fill_id,
            "broker": broker,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "timestamp": iso_now_taipei(),
            "fill_payload": payload,
            "dedupe": False,
            "lifecycle": lifecycle,
            "state_snapshot_before": before,
            "state_snapshot_after": after,
            "state_save_error": save_error,
        }
    )
    try:
        notify_fill_result(
            {
                "applied": save_error is None,
                "reason": save_error,
                "dedupe": False,
                "request_id": payload.get("request_id"),
                "fill_id": fill_id,
                "broker": broker,
                "broker_order_id": broker_order_id,
                "client_order_id": client_order_id,
                "pnl": payload.get("pnl"),
                "state_save_error": save_error,
                "lifecycle": lifecycle,
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] notify_fill_result failed: {exc}")
    return jsonify(
        {
            "status": "success",
            "request_id": payload.get("request_id"),
            "fill_id": fill_id,
            "broker": broker,
            "applied": save_error is None,
            "state_save_error": save_error,
            "lifecycle": lifecycle,
            "state": after,
        }
    ), 200


@app.route("/order-event", methods=["POST"])
def order_event():
    payload = request.get_json(silent=True)
    if payload is None:
        return _json_error(
            "Invalid or missing JSON body. Please send valid JSON with Content-Type: application/json.",
        )
    if not isinstance(payload, dict):
        return _json_error("JSON payload must be an object.")

    required = ["request_id", "event_type", "broker"]
    missing = [f for f in required if f not in payload]
    if missing:
        return _json_error("Missing required fields.", missing_fields=missing, required_fields=required)

    try:
        result = apply_order_event(payload, now_iso=iso_now_taipei())
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), status_code=400)

    _safe_log(
        {
            "event_type": "order_event_logged",
            "broker": payload.get("broker"),
            "timestamp": iso_now_taipei(),
            "raw_payload": payload,
            "lifecycle_before": result.get("before"),
            "lifecycle_after": result.get("after"),
        }
    )
    return jsonify({"status": "success", "lifecycle": result}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
