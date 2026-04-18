"""
Command Writer：將決策結果寫成 execution layer 可讀的 JSON，並追加訊號日誌（JSONL）。

輸出檔案（相對於專案執行時的當前工作目錄）：
- output/order_command.json（clear_order_command() 回傳是否曾存在主檔）
- output/order_command.json.tmp（原子寫入暫存，與 ORDER_COMMAND_TMP_PATH / _atomic_tmp_path 一致）
- output/signal_log.jsonl

所有檔案 I/O 使用 encoding="utf-8"。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Literal

from time_utils import iso_now_taipei

# --- 路徑（可依部署調整，集中於此） ---
OUTPUT_DIR = Path("output")
ORDER_COMMAND_PATH = OUTPUT_DIR / "order_command.json"
# 與 _atomic_write_json() 相同規則：原副檔名 + ".tmp"（例：order_command.json → order_command.json.tmp）
ORDER_COMMAND_TMP_PATH = ORDER_COMMAND_PATH.with_suffix(ORDER_COMMAND_PATH.suffix + ".tmp")
SIGNAL_LOG_PATH = OUTPUT_DIR / "signal_log.jsonl"


def _atomic_tmp_path(path: Path) -> Path:
    """原子寫入暫存路徑；須與 clear_order_command() 刪除的 tmp 一致。"""
    return path.with_suffix(path.suffix + ".tmp")

# --- 可擴充的 action 驗證 ---
# 預設允許值集中在此；未來擴充可：
# 1) 呼叫 extend_allowed_actions("NEW_ACTION")，或
# 2) 呼叫 write_order_command(..., allowed_actions={...}) 注入完整集合。
DEFAULT_ALLOWED_ACTIONS: frozenset[str] = frozenset({"CHASE", "RETEST"})
_EXTRA_ALLOWED_ACTIONS: set[str] = set()


def extend_allowed_actions(*actions: str) -> None:
    """在執行期擴充允許的 action（寫入層不重啟也可新增）。"""
    for a in actions:
        s = str(a).strip()
        if s:
            _EXTRA_ALLOWED_ACTIONS.add(s)


def _resolved_allowed_actions(override: Iterable[str] | None) -> frozenset[str]:
    if override is not None:
        return frozenset(str(x).strip() for x in override if str(x).strip())
    return DEFAULT_ALLOWED_ACTIONS | frozenset(_EXTRA_ALLOWED_ACTIONS)


def _iso_timestamp_taipei() -> str:
    """台北時區 ISO8601（秒精度，+08:00）。"""
    return iso_now_taipei()


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _build_command_id(timestamp_iso: str, symbol: str, action: str) -> str:
    """
    command_id 格式：{timestamp}|{symbol}|{action}
    與 timestamp / symbol / action 對齊，便於稽核與去重。
    """
    return f"{timestamp_iso}|{symbol}|{action}"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """
    先寫暫存檔、fsync，再以 os.replace 覆蓋正式檔，降低讀到半寫入 JSON 的機率。
    """
    _ensure_output_dir()
    tmp_path = _atomic_tmp_path(path)
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def write_order_command(
    command: dict[str, Any],
    *,
    allowed_actions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    寫入一筆交易指令到 output/order_command.json（覆蓋寫入）。

    寫入前會自動補上：
    - timestamp（台北時區 ISO8601，+08:00）
    - command_id（timestamp|symbol|action）

    輸出 schema 至少包含：action, symbol, price, plan, timestamp, command_id；
    若傳入含非空的 signal（str），則一併寫出；否則省略 signal 欄位。

    Parameters
    ----------
    command :
        必含 action, symbol, price, plan；可選 signal（str）。
    allowed_actions :
        若提供，則以此集合驗證 action；若為 None 則使用 DEFAULT + extend_allowed_actions 擴充結果。

    Returns
    -------
    dict
        實際寫入磁碟的指令 dict（含補齊欄位），方便呼叫端鏈式使用。

    Raises
    ------
    TypeError
        command 不是 dict，或 plan 不是 dict。
    ValueError
        缺欄位、型別不符、price 無法轉 float、action 不在允許集合。
    """
    if not isinstance(command, dict):
        raise TypeError("command 必須為 dict")

    out: dict[str, Any] = dict(command)

    required = ("action", "symbol", "price", "plan")
    missing = [k for k in required if k not in out]
    if missing:
        raise ValueError(f"缺少必要欄位: {missing}")

    if not isinstance(out["plan"], dict):
        raise TypeError("plan 必須為 dict")

    action = str(out["action"]).strip()
    if not action:
        raise ValueError("action 不可為空字串")
    allowed = _resolved_allowed_actions(allowed_actions)
    if action not in allowed:
        raise ValueError(f"不支援的 action={action!r}；允許: {sorted(allowed)}")

    symbol = str(out["symbol"]).strip()
    if not symbol:
        raise ValueError("symbol 不可為空字串")

    try:
        price_f = float(out["price"])
    except (TypeError, ValueError) as e:
        raise ValueError("price 必須可轉為 float") from e

    if "signal" in out:
        sig = out["signal"]
        if sig is None:
            out.pop("signal", None)
        elif isinstance(sig, str):
            if not sig.strip():
                out.pop("signal", None)
            else:
                out["signal"] = sig.strip()
        else:
            raise TypeError("signal 若提供則必須為 str 或 None")

    ts = _iso_timestamp_taipei()
    out["action"] = action
    out["symbol"] = symbol
    out["price"] = price_f
    out["timestamp"] = ts
    out["command_id"] = _build_command_id(ts, symbol, action)

    _atomic_write_json(ORDER_COMMAND_PATH, out)
    return out


OrderReadStatus = Literal[
    "ok",
    "missing",
    "empty",
    "read_error",
    "json_error",
    "not_dict",
]


def read_order_command_debug() -> tuple[dict[str, Any] | None, OrderReadStatus]:
    """
    讀取 output/order_command.json（除錯版）。

    與 read_order_command() 相同資料語意，但第二個回傳值標明原因，
    便於區分「檔案不存在」與「JSON 損毀」等情境。

    Returns
    -------
    tuple[dict | None, str]
        - ("ok",) 成功時：(dict, "ok")
        - 失敗時 payload 為 None，status 為：
          "missing" | "empty" | "read_error" | "json_error" | "not_dict"
    """
    if not ORDER_COMMAND_PATH.is_file():
        return None, "missing"
    try:
        raw = ORDER_COMMAND_PATH.read_text(encoding="utf-8")
    except OSError:
        return None, "read_error"
    if not raw.strip():
        return None, "empty"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "json_error"
    if not isinstance(data, dict):
        return None, "not_dict"
    return data, "ok"


def read_order_command() -> dict[str, Any] | None:
    """
    讀取 output/order_command.json。

    Returns
    -------
    dict | None
        檔案不存在、為空、或內容非合法 JSON 時回傳 None；
        成功解析則回傳 dict。

    除錯請用 read_order_command_debug() 取得失敗原因。
    """
    payload, status = read_order_command_debug()
    return payload if status == "ok" else None


def clear_order_command() -> bool:
    """
    清除指令檔：刪除 output/order_command.json（若存在），
    並一併刪除與 _atomic_write_json() 相同命名的暫存檔（ORDER_COMMAND_TMP_PATH）。

    Returns
    -------
    bool
        True：主檔 order_command.json 在清除前存在（代表這次有清掉主指令檔）。
        False：主檔本來就不存在（仍可能順手刪除了殘留的 .tmp）。
    """
    main_existed = ORDER_COMMAND_PATH.is_file()
    for path in (ORDER_COMMAND_PATH, ORDER_COMMAND_TMP_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return main_existed


def append_signal_log(record: dict[str, Any]) -> dict[str, Any]:
    """
    追加一筆訊號紀錄到 output/signal_log.jsonl。

    會保留傳入 record 的所有欄位（淺拷貝）：
    - 若 record 已含 timestamp，一律保留原值、不覆寫。
    - 一律新增 logged_at（台北時區 ISO8601 +08:00），代表寫入日誌的時間。

    Returns
    -------
    dict
        實際寫入該行的 dict（含 logged_at；若有則含原始 timestamp）。
    """
    if not isinstance(record, dict):
        raise TypeError("record 必須為 dict")

    line_obj: dict[str, Any] = dict(record)
    line_obj["logged_at"] = _iso_timestamp_taipei()

    _ensure_output_dir()
    payload = json.dumps(line_obj, ensure_ascii=False, separators=(",", ":"))
    with open(SIGNAL_LOG_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(payload)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    return line_obj
