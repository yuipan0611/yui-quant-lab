from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from time_utils import TAIPEI_TZ, now_taipei, parse_iso_dt

STATE_SCHEMA_VERSION = 1

REGIME_UNKNOWN = "unknown"
REGIME_NORMAL = "normal"
REGIME_TREND = "trend"
REGIME_RANGE = "range"
REGIME_HIGH_VOL = "high_vol"
ALLOWED_REGIMES = {
    REGIME_UNKNOWN,
    REGIME_NORMAL,
    REGIME_TREND,
    REGIME_RANGE,
    REGIME_HIGH_VOL,
}

OUTPUT_DIR = Path("output")
STATE_PATH = OUTPUT_DIR / "state.json"
FILL_DEDUPE_PATH = OUTPUT_DIR / "fill_request_ids.json"
_MAX_FILL_IDS = 5000


class TradingState(TypedDict):
    version: int
    trading_day: str
    today_realized_pnl: float
    today_loss: float
    consecutive_loss: int
    cooldown_until: str | None
    regime: str
    lock_reason: str | None
    daily_trade_count: int
    last_signal_ts: str | None
    last_decision: str | None
    last_decision_reason: str | None
    updated_at: str


def _safe_dt(value: datetime | None) -> datetime:
    if value is None:
        return now_taipei()
    if value.tzinfo is None:
        return value.replace(tzinfo=TAIPEI_TZ)
    return value.astimezone(TAIPEI_TZ)


def _default_state(now: datetime | None = None) -> TradingState:
    dt = _safe_dt(now)
    return {
        "version": STATE_SCHEMA_VERSION,
        "trading_day": dt.date().isoformat(),
        "today_realized_pnl": 0.0,
        "today_loss": 0.0,
        "consecutive_loss": 0,
        "cooldown_until": None,
        "regime": REGIME_UNKNOWN,
        "lock_reason": None,
        "daily_trade_count": 0,
        "last_signal_ts": None,
        "last_decision": None,
        "last_decision_reason": None,
        "updated_at": dt.isoformat(timespec="seconds"),
    }


def _sanitize_state(state: dict[str, Any], now: datetime | None = None) -> TradingState:
    base = _default_state(now=now)
    base["version"] = int(state.get("version", STATE_SCHEMA_VERSION))
    base["trading_day"] = str(state.get("trading_day", base["trading_day"]))
    if "today_realized_pnl" in state:
        pnl_sum = float(state["today_realized_pnl"])
    else:
        pnl_sum = float(state.get("today_loss", 0.0))
    base["today_realized_pnl"] = pnl_sum
    base["today_loss"] = pnl_sum
    base["consecutive_loss"] = int(state.get("consecutive_loss", 0))
    cooldown_raw = state.get("cooldown_until")
    base["cooldown_until"] = str(cooldown_raw) if cooldown_raw else None
    regime = str(state.get("regime", REGIME_UNKNOWN)).strip().lower()
    base["regime"] = regime if regime in ALLOWED_REGIMES else REGIME_UNKNOWN
    lock_reason = state.get("lock_reason")
    base["lock_reason"] = str(lock_reason) if lock_reason else None
    base["daily_trade_count"] = int(state.get("daily_trade_count", 0))
    last_signal_ts = state.get("last_signal_ts")
    base["last_signal_ts"] = str(last_signal_ts) if last_signal_ts else None
    last_decision = state.get("last_decision")
    base["last_decision"] = str(last_decision) if last_decision else None
    last_reason = state.get("last_decision_reason")
    base["last_decision_reason"] = str(last_reason) if last_reason else None
    updated_at = state.get("updated_at")
    parsed_updated_at = parse_iso_dt(updated_at) if isinstance(updated_at, str) else None
    base["updated_at"] = (
        parsed_updated_at.isoformat(timespec="seconds")
        if parsed_updated_at
        else base["updated_at"]
    )
    return base


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _ensure_output_dir()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _archive_corrupt_state(raw: str, now: datetime | None = None) -> Path:
    _ensure_output_dir()
    dt = _safe_dt(now)
    suffix = dt.strftime("%Y%m%dT%H%M%S")
    backup_path = OUTPUT_DIR / f"state.json.corrupt.{suffix}"
    backup_path.write_text(raw, encoding="utf-8")
    return backup_path


def load_state(now: datetime | None = None) -> TradingState:
    dt = _safe_dt(now)
    if not STATE_PATH.is_file():
        return _default_state(now=dt)
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("state json is not an object")
    except (json.JSONDecodeError, OSError, ValueError):
        try:
            raw_fallback = STATE_PATH.read_text(encoding="utf-8")
            _archive_corrupt_state(raw_fallback, now=dt)
        except OSError:
            pass
        return _default_state(now=dt)
    return _sanitize_state(data, now=dt)


def save_state(state: dict[str, Any]) -> None:
    normalized = _sanitize_state(state)
    _atomic_write_json(STATE_PATH, normalized)


def reset_state_if_new_day(
    state: dict[str, Any], now: datetime | None = None
) -> TradingState:
    dt = _safe_dt(now)
    normalized = _sanitize_state(state, now=dt)
    today = dt.date().isoformat()
    if normalized["trading_day"] == today:
        return normalized

    normalized["trading_day"] = today
    normalized["today_realized_pnl"] = 0.0
    normalized["today_loss"] = 0.0
    normalized["consecutive_loss"] = 0
    normalized["cooldown_until"] = None
    normalized["lock_reason"] = None
    normalized["daily_trade_count"] = 0
    normalized["last_signal_ts"] = None
    normalized["last_decision"] = None
    normalized["last_decision_reason"] = None
    normalized["regime"] = REGIME_UNKNOWN
    normalized["updated_at"] = dt.isoformat(timespec="seconds")
    return normalized


def evaluate_state_gate(
    state: dict[str, Any], signal_payload: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    _ = signal_payload
    dt = _safe_dt(now)
    normalized = _sanitize_state(state, now=dt)
    cooldown_dt = parse_iso_dt(normalized["cooldown_until"])
    if cooldown_dt and dt < cooldown_dt:
        normalized["lock_reason"] = "cooldown_active"
        return {
            "allowed": False,
            "decision": "SKIP",
            "reason": "cooldown_active",
            "details": {
                "cooldown_until": cooldown_dt.isoformat(timespec="seconds"),
            },
        }

    details: dict[str, Any] = {}
    if normalized["regime"] == REGIME_HIGH_VOL:
        details["high_vol_guard"] = True

    return {
        "allowed": True,
        "decision": None,
        "reason": "state_gate_passed",
        "details": details,
    }


def apply_decision_effects(
    state: dict[str, Any], decision_result: dict[str, Any], now: datetime | None = None
) -> TradingState:
    dt = _safe_dt(now)
    normalized = _sanitize_state(state, now=dt)
    decision = str(decision_result.get("decision", "")).strip() or None
    reason = str(decision_result.get("reason", "")).strip() or None

    normalized["last_decision"] = decision
    normalized["last_decision_reason"] = reason
    normalized["last_signal_ts"] = dt.isoformat(timespec="seconds")
    normalized["updated_at"] = dt.isoformat(timespec="seconds")
    normalized["lock_reason"] = None

    if decision in ("CHASE", "RETEST"):
        normalized["daily_trade_count"] += 1

    return normalized


def apply_fill_result(
    state: dict[str, Any], fill_result: dict[str, Any], now: datetime | None = None
) -> TradingState:
    """
    Apply realized PnL from execution layer.

    - today_realized_pnl: cumulative realized PnL for trading_day (signed; + profit, - loss).
    - today_loss: legacy alias, always kept equal to today_realized_pnl for backward compatibility.
    - consecutive_loss: increments only on pnl < 0; resets to 0 on pnl > 0; unchanged on pnl == 0.
    """
    dt = _safe_dt(now)
    normalized = _sanitize_state(deepcopy(state), now=dt)

    pnl = fill_result.get("pnl")
    if pnl is not None:
        pnl_f = float(pnl)
        new_sum = float(normalized["today_realized_pnl"]) + pnl_f
        normalized["today_realized_pnl"] = new_sum
        normalized["today_loss"] = new_sum
        if pnl_f < 0:
            normalized["consecutive_loss"] = int(normalized["consecutive_loss"]) + 1
        elif pnl_f > 0:
            normalized["consecutive_loss"] = 0
        # pnl_f == 0: consecutive_loss unchanged (explicit)

    cooldown_minutes = fill_result.get("cooldown_minutes")
    if cooldown_minutes is not None:
        minutes = max(0, int(cooldown_minutes))
        if minutes > 0:
            normalized["cooldown_until"] = (dt + timedelta(minutes=minutes)).isoformat(
                timespec="seconds"
            )
            normalized["lock_reason"] = "cooldown_active"

    regime = fill_result.get("regime")
    if regime is not None:
        regime_s = str(regime).strip().lower()
        normalized["regime"] = regime_s if regime_s in ALLOWED_REGIMES else REGIME_UNKNOWN

    normalized["updated_at"] = dt.isoformat(timespec="seconds")
    return normalized


def _default_fill_dedupe() -> dict[str, Any]:
    return {"version": 2, "processed_keys": []}


def _normalize_processed_keys(data: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    ver = int(data.get("version", 1))
    pk = data.get("processed_keys")
    if isinstance(pk, list):
        keys.extend(str(x).strip() for x in pk if str(x).strip())
    if ver < 2:
        legacy = data.get("request_ids")
        if isinstance(legacy, list):
            for x in legacy:
                s = str(x).strip()
                if s:
                    keys.append(f"req:{s}")
    # de-dupe preserving order
    seen: set[str] = set()
    out_keys: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out_keys.append(k)
    return out_keys[-_MAX_FILL_IDS:]


def _load_fill_dedupe() -> dict[str, Any]:
    if not FILL_DEDUPE_PATH.is_file():
        return _default_fill_dedupe()
    try:
        raw = FILL_DEDUPE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        data["processed_keys"] = _normalize_processed_keys(data)
        data["version"] = 2
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return _default_fill_dedupe()


def _fill_dedupe_key(fill_id: str | None, request_id: str | None) -> str | None:
    fid = str(fill_id).strip() if fill_id else ""
    rid = str(request_id).strip() if request_id else ""
    if fid:
        return f"fill:{fid}"
    if rid:
        return f"req:{rid}"
    return None


def is_fill_processed(
    *,
    fill_id: str | None = None,
    request_id: str | None = None,
) -> bool:
    key = _fill_dedupe_key(fill_id, request_id)
    if not key:
        return False
    data = _load_fill_dedupe()
    return key in set(data.get("processed_keys", []))


def record_fill_processed(
    *,
    fill_id: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Record processed fill for deduplication (atomic write).
    Prefer fill_id when present; otherwise fall back to request_id.
    """
    _ = now
    key = _fill_dedupe_key(fill_id, request_id)
    if not key:
        return
    data = _load_fill_dedupe()
    keys = list(data.get("processed_keys", []))
    if key not in keys:
        keys.append(key)
    keys = keys[-_MAX_FILL_IDS:]
    out = {"version": 2, "processed_keys": keys}
    _atomic_write_json(FILL_DEDUPE_PATH, out)


def is_fill_request_id_processed(request_id: str) -> bool:
    return is_fill_processed(fill_id=None, request_id=request_id)


def record_fill_request_id(request_id: str, now: datetime | None = None) -> None:
    record_fill_processed(fill_id=None, request_id=request_id, now=now)
