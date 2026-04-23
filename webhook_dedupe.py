"""
Webhook 短時去重（單機、檔案型、best-effort）。

為什麼需要它？
- TradingView / 網路中間層偶爾會「重送同一包 JSON」。
- 若不做短時去重，可能重複跑 decision、重複寫 signal log，未來也可能演變成重複下單風險。

這不是什麼？
- 不是 Redis、不是分散式鎖、也不是「永久冪等」。
- 只在「同一台機器 + 短時間窗」內，盡量避免明顯重複。

指紋（fingerprint）怎麼做？
- 先把 payload 裡「每次請求都會變」的雜訊欄位拿掉（欄位名稱不分大小寫），
  再對 dict/list 遞迴處理、排序鍵後做 JSON 正規化，最後 SHA256(endpoint + 正規化 JSON)。
- 一定會拿掉：TradingView 的 secret（避免把密鑰寫進去重檔，也避免密鑰輪替讓同一訊號對不起來）。
- 也會拿掉（範例，完整清單見程式常數）：request_id、各種 *_at / timestamp / time、
  nonce、uuid、id、event_id、message_id、update_id、callback_query_id、alert_id、
  bar_time、unix_time、exchange_timestamp、meta 等「時間或唯一識別」意味的欄位。
- 刻意保留：symbol、signal、price、breakout_level、delta_strength、timeframe、
  nq_eod、qqq_intraday、levels、bias、regime、broker、client_order_id 等策略相關欄位
  （若你希望連 client_order_id 也排除，請改走不同 payload 設計；此版視為訊號的一部分）。

限制（白話）：
- 若你在 payload 裡塞了「每次都變、但又不在我們清單內」的欄位，去重可能失效；
  這版刻意用「可讀的清單規則」，不把邏輯做成黑魔法。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

# 這些欄位「名稱」會從指紋計算用的樹狀資料中移除（不分大小寫比對）。
# 設計理由：它們多半代表「這次 HTTP 的唯一性 / 時間 / 外部事件序號」，
# 而不是策略訊號本體（例如 symbol、signal、價位、delta 等）。
_IDEMPOTENCY_STRIP_KEYS_LOWER = frozenset(
    {
        "secret",
        "request_id",
        "timestamp",
        "timestamps",
        "generated_at",
        "created_at",
        "updated_at",
        "ingested_at",
        "received_at",
        "sent_at",
        "fired_at",
        "delivery_time",
        "webhook_timestamp",
        "time",
        "ts",
        "nonce",
        "uuid",
        "event_id",
        "message_id",
        "update_id",
        "callback_query_id",
        "callback_id",
        "alert_id",
        "trigger_id",
        "timenow",
        "time_now",
        "meta",
        "bar_time",
        "last_bar_time",
        "server_time",
        "unix_time",
        "exchange_timestamp",
        "id",
    }
)


def _strip_idempotency_noise(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                k = str(k)
            if k.lower() in _IDEMPOTENCY_STRIP_KEYS_LOWER:
                continue
            out[k] = _strip_idempotency_noise(v)
        return out
    if isinstance(value, list):
        return [_strip_idempotency_noise(x) for x in value]
    return value


def fingerprint_for(endpoint: str, payload: dict) -> str:
    """回傳十六進位 SHA256（64 字元）。"""
    cleaned = _strip_idempotency_noise(payload)
    canonical = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raw = f"{endpoint}\n{canonical}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def get_dedupe_path() -> Path:
    raw = os.environ.get("WEBHOOK_DEDUPE_PATH")
    root = Path(__file__).resolve().parent
    if raw is not None and str(raw).strip():
        return Path(str(raw).strip())
    return root / "output" / "webhook_dedupe.json"


def _dedupe_path() -> Path:
    return get_dedupe_path()


def _ttl_seconds() -> int:
    raw = os.environ.get("WEBHOOK_DEDUPE_TTL_SEC", "300")
    try:
        n = int(str(raw).strip())
    except ValueError:
        return 300
    return max(30, min(n, 86400))


def _load_store(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _prune_store(store: dict[str, float], now: float) -> dict[str, float]:
    return {k: exp for k, exp in store.items() if exp > now}


def _atomic_write_json(path: Path, obj: object) -> None:
    """
    原子寫入：先寫同目錄暫存檔，再 os.replace 覆蓋正式檔。
    目的：避免寫到一半當機，留下半套 JSON 讓下次讀取炸掉。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def is_duplicate(endpoint: str, payload: dict) -> bool:
    fp = fingerprint_for(endpoint, payload)
    path = _dedupe_path()
    now = time.time()
    store = _prune_store(_load_store(path), now)
    exp = store.get(fp)
    return exp is not None and exp > now


def remember(endpoint: str, payload: dict) -> None:
    fp = fingerprint_for(endpoint, payload)
    path = _dedupe_path()
    now = time.time()
    ttl = float(_ttl_seconds())
    store = _prune_store(_load_store(path), now)
    store[fp] = now + ttl
    # 避免檔案無限長大：超量就砍掉最舊的（仍以過期時間為主）。
    if len(store) > 5000:
        for k in sorted(store, key=lambda x: store[x])[:1000]:
            store.pop(k, None)
    _atomic_write_json(path, store)
