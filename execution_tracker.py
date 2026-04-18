from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from time_utils import iso_now_taipei

OUTPUT_DIR = Path("output")
EXECUTION_EVENTS_PATH = OUTPUT_DIR / "execution_events.jsonl"
ORDERS_DIR = OUTPUT_DIR / "orders"
TRACKER_SCHEMA_VERSION = 2

DEFAULT_BROKER_KEY = "single_broker"

CommandLifecycleStatus = Literal[
    "created",
    "dispatched",
    "acknowledged",
    "filled",
    "rejected",
    "cancelled",
    "expired",
]

FillLifecycleStatus = Literal["pending", "partial", "filled", "none"]


def _ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _ensure_dirs()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _order_path(request_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in request_id.strip())
    return ORDERS_DIR / f"{safe}.json"


def _append_event(record: dict[str, Any]) -> dict[str, Any]:
    _ensure_dirs()
    line = dict(record)
    line.setdefault("logged_at", iso_now_taipei())
    payload = json.dumps(line, ensure_ascii=False, separators=(",", ":"))
    with open(EXECUTION_EVENTS_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(payload)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    return line


def log_execution_event(record: dict[str, Any]) -> dict[str, Any]:
    """Public helper for app-layer events (e.g. unlinked fills)."""
    return _append_event(record)


def _empty_broker_state() -> dict[str, Any]:
    return {
        "command_status": "created",
        "broker_order_id": None,
        "client_order_id": None,
        "fill_status": "pending",
        "filled_qty": 0.0,
        "avg_fill_price": None,
        "rejection_reason": None,
        "last_fill_id": None,
        "updated_at": None,
    }


_STATUS_RANK: dict[str, int] = {
    "created": 0,
    "dispatched": 1,
    "acknowledged": 2,
    "filled": 3,
    "expired": 4,
    "cancelled": 5,
    "rejected": 6,
}

_FILL_RANK: dict[str, int] = {
    "pending": 0,
    "partial": 1,
    "filled": 2,
    "none": 3,
}


def _max_status(statuses: list[str], rank: dict[str, int]) -> str:
    best = statuses[0] if statuses else "created"
    best_r = rank.get(best, -1)
    for s in statuses[1:]:
        r = rank.get(s, -1)
        if r > best_r:
            best, best_r = s, r
    return best


def _aggregate_from_brokers(brokers: dict[str, Any]) -> dict[str, Any]:
    cmd_list: list[str] = []
    fill_list: list[str] = []
    any_broker_id: str | None = None
    any_client_id: str | None = None
    for _bk, bstate in brokers.items():
        if not isinstance(bstate, dict):
            continue
        cmd_list.append(str(bstate.get("command_status", "created")))
        fill_list.append(str(bstate.get("fill_status", "pending")))
        bo = bstate.get("broker_order_id")
        if bo and not any_broker_id:
            any_broker_id = str(bo).strip() or None
        co = bstate.get("client_order_id")
        if co and not any_client_id:
            any_client_id = str(co).strip() or None
    return {
        "command_status": _max_status(cmd_list, _STATUS_RANK),
        "fill_status": _max_status(fill_list, _FILL_RANK),
        "broker_order_id": any_broker_id,
        "client_order_id": any_client_id,
    }


def _migrate_legacy_record(rec: dict[str, Any], ts: str) -> dict[str, Any]:
    """v1 flat record -> v2 brokers[DEFAULT_BROKER_KEY]."""
    if int(rec.get("version", 1)) >= 2 and isinstance(rec.get("brokers"), dict):
        return rec
    b = _empty_broker_state()
    b["command_status"] = str(rec.get("command_status", "created"))
    b["broker_order_id"] = rec.get("broker_order_id")
    b["client_order_id"] = rec.get("client_order_id")
    b["fill_status"] = str(rec.get("fill_status", "pending"))
    try:
        b["filled_qty"] = float(rec.get("filled_qty", 0) or 0)
    except (TypeError, ValueError):
        b["filled_qty"] = 0.0
    b["avg_fill_price"] = rec.get("avg_fill_price")
    b["rejection_reason"] = rec.get("rejection_reason")
    b["last_fill_id"] = rec.get("last_fill_id")
    b["updated_at"] = rec.get("updated_at") or ts
    rec["brokers"] = {DEFAULT_BROKER_KEY: b}
    rec["version"] = TRACKER_SCHEMA_VERSION
    agg = _aggregate_from_brokers(rec["brokers"])
    rec["command_status"] = agg["command_status"]
    rec["fill_status"] = agg["fill_status"]
    rec["broker_order_id"] = agg["broker_order_id"]
    rec["client_order_id"] = agg["client_order_id"]
    return rec


def normalize_order_record(rec: dict[str, Any], ts: str | None = None) -> dict[str, Any]:
    t = ts or iso_now_taipei()
    if not isinstance(rec.get("brokers"), dict):
        rec = _migrate_legacy_record(dict(rec), t)
    brokers_raw = rec.get("brokers")
    if not isinstance(brokers_raw, dict):
        brokers_raw = {}
    brokers: dict[str, Any] = {}
    for bk, bstate in brokers_raw.items():
        key = str(bk).strip() or DEFAULT_BROKER_KEY
        if not isinstance(bstate, dict):
            bstate = _empty_broker_state()
        bs = dict(bstate)
        defaults = _empty_broker_state()
        for k, default in defaults.items():
            bs.setdefault(k, default)
        brokers[key] = bs
    rec["brokers"] = brokers
    rec["version"] = TRACKER_SCHEMA_VERSION
    agg = _aggregate_from_brokers(rec["brokers"])
    rec["command_status"] = agg["command_status"]
    rec["fill_status"] = agg["fill_status"]
    rec["broker_order_id"] = agg["broker_order_id"]
    rec["client_order_id"] = agg["client_order_id"]
    rec.setdefault("created_at", t)
    rec["updated_at"] = t
    return rec


def load_order_record(request_id: str) -> dict[str, Any] | None:
    path = _order_path(request_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return normalize_order_record(data)
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_broker_key(broker: str | None) -> str:
    b = str(broker or "").strip()
    return b if b else DEFAULT_BROKER_KEY


def create_order_record(
    *,
    request_id: str,
    symbol: str,
    decision: str,
    broker: str | None = None,
    client_order_id: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    ts = now_iso or iso_now_taipei()
    bk = _resolve_broker_key(broker)
    bstate = _empty_broker_state()
    bstate["client_order_id"] = str(client_order_id).strip() if client_order_id else None
    bstate["updated_at"] = ts

    rid = str(request_id).strip()
    existing = load_order_record(rid)
    if existing is None:
        rec = {
            "version": TRACKER_SCHEMA_VERSION,
            "request_id": rid,
            "symbol": str(symbol).strip(),
            "decision": str(decision).strip(),
            "brokers": {bk: bstate},
            "created_at": ts,
            "updated_at": ts,
        }
    else:
        rec = normalize_order_record(existing, ts)
        brokers = rec.setdefault("brokers", {})
        if not isinstance(brokers, dict):
            rec["brokers"] = {}
            brokers = rec["brokers"]
        if bk in brokers and isinstance(brokers[bk], dict):
            cur = dict(brokers[bk])
            if client_order_id:
                cur["client_order_id"] = str(client_order_id).strip()
            brokers[bk] = normalize_order_record({"brokers": {bk: cur}}, ts)["brokers"][bk]
        else:
            brokers[bk] = bstate
        rec["updated_at"] = ts

    rec = normalize_order_record(rec, ts)
    _atomic_write_json(_order_path(rec["request_id"]), rec)
    _append_event(
        {
            "event_type": "order_lifecycle",
            "sub_type": "created",
            "broker": bk,
            "request_id": rec["request_id"],
            "symbol": rec["symbol"],
            "decision": rec["decision"],
            "command_status": rec["brokers"][bk]["command_status"],
            "fill_status": rec["brokers"][bk]["fill_status"],
        }
    )
    return rec


def apply_order_event(payload: dict[str, Any], now_iso: str | None = None) -> dict[str, Any]:
    """
    Apply execution-layer event (ack / reject / cancel / dispatch) for a specific broker.
    """
    ts = now_iso or iso_now_taipei()
    rid = str(payload.get("request_id", "")).strip()
    if not rid:
        raise ValueError("request_id is required")

    broker_raw = payload.get("broker")
    if broker_raw is None or str(broker_raw).strip() == "":
        bk = DEFAULT_BROKER_KEY
    else:
        bk = str(broker_raw).strip()

    event_type = str(payload.get("event_type", "")).strip().lower()
    if not event_type:
        raise ValueError("event_type is required")

    rec = load_order_record(rid)
    if rec is None:
        rec = {
            "version": TRACKER_SCHEMA_VERSION,
            "request_id": rid,
            "symbol": str(payload.get("symbol", "")).strip() or "UNKNOWN",
            "decision": str(payload.get("decision", "")).strip() or "UNKNOWN",
            "brokers": {},
            "created_at": ts,
            "updated_at": ts,
        }

    rec = normalize_order_record(rec, ts)
    brokers: dict[str, Any] = rec["brokers"]
    if bk not in brokers:
        brokers[bk] = _empty_broker_state()
    bstate = brokers[bk]

    before = deepcopy(rec)

    broker_order_id = payload.get("broker_order_id")
    if broker_order_id is not None:
        bstate["broker_order_id"] = str(broker_order_id).strip() or None

    client_order_id = payload.get("client_order_id")
    if client_order_id is not None:
        bstate["client_order_id"] = str(client_order_id).strip() or None

    reason = payload.get("reason")
    if reason is not None:
        bstate["rejection_reason"] = str(reason).strip() or None

    if event_type == "order_dispatched":
        bstate["command_status"] = "dispatched"
    elif event_type == "order_acknowledged":
        bstate["command_status"] = "acknowledged"
    elif event_type == "order_rejected":
        bstate["command_status"] = "rejected"
        bstate["fill_status"] = "none"
    elif event_type == "order_cancelled":
        bstate["command_status"] = "cancelled"
    elif event_type == "order_expired":
        bstate["command_status"] = "expired"
    else:
        raise ValueError(f"unsupported event_type={event_type!r}")

    bstate["updated_at"] = ts
    rec = normalize_order_record(rec, ts)
    _atomic_write_json(_order_path(rid), rec)

    _append_event(
        {
            "event_type": "order_event",
            "sub_type": event_type,
            "broker": bk,
            "request_id": rid,
            "symbol": rec.get("symbol"),
            "command_status": bstate.get("command_status"),
            "fill_status": bstate.get("fill_status"),
            "broker_order_id": bstate.get("broker_order_id"),
            "client_order_id": bstate.get("client_order_id"),
            "reason": bstate.get("rejection_reason"),
            "raw_payload": payload,
        }
    )
    return {"before": before, "after": rec}


def apply_fill_to_order(
    *,
    request_id: str | None,
    broker: str | None,
    broker_order_id: str | None,
    client_order_id: str | None,
    fill_id: str | None,
    pnl: float | None,
    filled_qty: float | None,
    avg_fill_price: float | None,
    now_iso: str | None = None,
) -> dict[str, Any] | None:
    """
    Link fill to order record.
    Matching priority:
    1) request_id file
    2) broker + broker_order_id within brokers[broker]
    3) broker_order_id scan across all brokers (legacy / unknown broker key)
    4) client_order_id scan across all brokers
    """
    ts = now_iso or iso_now_taipei()
    rec: dict[str, Any] | None = None
    match_key: str | None = None
    matched_broker: str | None = None

    if request_id and str(request_id).strip():
        rec = load_order_record(str(request_id).strip())
        if rec:
            match_key = "request_id"

    bid = str(broker_order_id).strip() if broker_order_id else ""
    br = str(broker).strip() if broker else ""

    if rec is None and bid and br:
        _ensure_dirs()
        for path in ORDERS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            norm = normalize_order_record(data, ts)
            bstate = norm.get("brokers", {}).get(br)
            if isinstance(bstate, dict) and str(bstate.get("broker_order_id", "")).strip() == bid:
                rec = norm
                match_key = "broker+broker_order_id"
                matched_broker = br
                break

    if rec is None and bid:
        _ensure_dirs()
        for path in ORDERS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            norm = normalize_order_record(data, ts)
            for bk, bstate in norm.get("brokers", {}).items():
                if not isinstance(bstate, dict):
                    continue
                if str(bstate.get("broker_order_id", "")).strip() == bid:
                    rec = norm
                    match_key = "broker_order_id"
                    matched_broker = str(bk)
                    break
            if rec:
                break

    cid = str(client_order_id).strip() if client_order_id else ""
    if rec is None and cid:
        _ensure_dirs()
        for path in ORDERS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            norm = normalize_order_record(data, ts)
            for bk, bstate in norm.get("brokers", {}).items():
                if not isinstance(bstate, dict):
                    continue
                if str(bstate.get("client_order_id", "")).strip() == cid:
                    rec = norm
                    match_key = "client_order_id"
                    matched_broker = str(bk)
                    break
            if rec:
                break

    if rec is None:
        return None

    rec = normalize_order_record(rec, ts)
    brokers: dict[str, Any] = rec["brokers"]

    if matched_broker is None:
        if request_id and str(request_id).strip():
            if br and br in brokers:
                matched_broker = br
            elif bid:
                for bk, bstate in brokers.items():
                    if not isinstance(bstate, dict):
                        continue
                    if str(bstate.get("broker_order_id", "")).strip() == bid:
                        matched_broker = str(bk)
                        break
            if matched_broker is None:
                matched_broker = (
                    DEFAULT_BROKER_KEY
                    if DEFAULT_BROKER_KEY in brokers
                    else (next(iter(brokers.keys()), DEFAULT_BROKER_KEY))
                )
        else:
            matched_broker = matched_broker or DEFAULT_BROKER_KEY

    if matched_broker not in brokers:
        brokers[matched_broker] = _empty_broker_state()
    bstate = brokers[matched_broker]

    before = deepcopy(rec)

    if fill_id:
        bstate["last_fill_id"] = str(fill_id).strip()
    if filled_qty is not None:
        try:
            bstate["filled_qty"] = float(bstate.get("filled_qty", 0) or 0) + float(filled_qty)
        except (TypeError, ValueError):
            pass
    if avg_fill_price is not None:
        try:
            bstate["avg_fill_price"] = float(avg_fill_price)
        except (TypeError, ValueError):
            bstate["avg_fill_price"] = None

    if filled_qty is not None and float(filled_qty) > 0:
        bstate["fill_status"] = "partial"
    if pnl is not None:
        bstate["fill_status"] = "filled"
        bstate["command_status"] = "filled"

    bstate["updated_at"] = ts
    rec = normalize_order_record(rec, ts)
    rid = str(rec["request_id"])
    _atomic_write_json(_order_path(rid), rec)

    _append_event(
        {
            "event_type": "fill_linked",
            "broker": matched_broker,
            "request_id": rid,
            "matched_by": match_key,
            "fill_id": fill_id,
            "broker_order_id": broker_order_id or bstate.get("broker_order_id"),
            "client_order_id": client_order_id or bstate.get("client_order_id"),
            "pnl": pnl,
            "filled_qty_delta": filled_qty,
            "avg_fill_price": avg_fill_price,
            "command_status": bstate.get("command_status"),
            "fill_status": bstate.get("fill_status"),
        }
    )
    return {"before": before, "after": rec}
