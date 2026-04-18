"""
Telegram：決策／成交通知、Webhook 指令（MVP）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from command_writer import SIGNAL_LOG_PATH
from state_manager import load_state, reset_state_if_new_day

TELEGRAM_API = "https://api.telegram.org"


def _is_notify_enabled() -> bool:
    return os.getenv("ENABLE_TELEGRAM_NOTIFY", "false").strip().lower() == "true"


def _telegram_decision_notify_mode() -> tuple[bool, str]:
    """
    決策通知開關與離線模式標籤。
    回傳 (should_call_telegram_api, offline_mode)。
    should_call_telegram_api 為 False 時，offline_mode 為 print 或 disabled；
    為 True 時 offline_mode 為空字串（呼叫端應改走 telegram / missing_credentials）。
    - 未設定或空字串：不呼叫 API，mode=print（本機預設可掃 log）
    - 明確非 true：不呼叫 API，mode=disabled
    """
    raw = os.getenv("ENABLE_TELEGRAM_NOTIFY")
    if raw is None or str(raw).strip() == "":
        return False, "print"
    if str(raw).strip().lower() == "true":
        return True, ""
    return False, "disabled"


def _env_token() -> str | None:
    t = os.getenv("TELEGRAM_BOT_TOKEN")
    return str(t).strip() if t else None


def _env_chat_id() -> str | None:
    c = os.getenv("TELEGRAM_CHAT_ID")
    return str(c).strip() if c else None


def _webhook_secret_expected() -> str | None:
    s = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    return str(s).strip() if s else None


def tail_jsonl_find_last(
    path: Path,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    max_lines: int = 4096,
) -> dict[str, Any] | None:
    """
    自 JSONL 檔尾向前掃描，回傳最後一筆滿足 predicate 的 JSON 物件（須為 dict）。
    檔案不存在、無法讀取、無有效 JSON 行則回傳 None。
    max_lines：最多檢視檔案結尾的行數（含空行／壞行）。
    """
    if max_lines <= 0:
        return None
    if not path.is_file():
        return None
    try:
        lines = _read_tail_text_lines(path, max_lines)
    except OSError:
        return None
    for line in reversed(lines):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and predicate(obj):
            return obj
    return None


def _read_tail_text_lines(path: Path, max_lines: int) -> list[str]:
    """讀取檔案結尾最多 max_lines 行（以 \\n 分隔）；大檔僅讀尾端區塊以避免載入全文。"""
    if max_lines <= 0:
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []
    tail_window = 2 * 1024 * 1024
    with open(path, "rb") as f:
        if size <= tail_window:
            data = f.read()
        else:
            f.seek(max(0, size - tail_window))
            data = f.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > tail_window and lines:
        lines = lines[1:]
    return lines[-max_lines:]


def extract_update_fields(update: Any) -> dict[str, Any]:
    """
    容錯抽取 Telegram Update 的 chat_id、text、update_type。
    非所有 update 都一定有 message.text。
    """
    out: dict[str, Any] = {"update_type": None, "chat_id": None, "text": None}
    if not isinstance(update, dict):
        return out

    def _coerce_chat_id(raw: Any) -> int | None:
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            try:
                return int(raw.strip())
            except ValueError:
                return None
        return None

    def _from_message_container(key: str) -> None:
        container = update.get(key)
        if not isinstance(container, dict):
            return
        out["update_type"] = key
        chat = container.get("chat")
        if isinstance(chat, dict):
            out["chat_id"] = _coerce_chat_id(chat.get("id"))
        text = container.get("text")
        if isinstance(text, str):
            out["text"] = text.strip() or None
        elif text is not None:
            out["text"] = str(text).strip() or None

    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        if key in update:
            _from_message_container(key)
            if out["update_type"] is not None:
                return out

    cq = update.get("callback_query")
    if isinstance(cq, dict):
        out["update_type"] = "callback_query"
        msg = cq.get("message")
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if isinstance(chat, dict):
                out["chat_id"] = _coerce_chat_id(chat.get("id"))
        data = cq.get("data")
        if isinstance(data, str):
            out["text"] = data.strip() or None
        return out

    if update:
        out["update_type"] = "unknown"
    return out


def _authorized_chat_id(chat_id: Any) -> bool:
    if chat_id is None:
        return False
    allowed = _env_chat_id()
    if not allowed:
        return False
    return str(allowed) == str(chat_id)


def verify_telegram_webhook_secret(headers: Any) -> bool:
    """若未設定 TELEGRAM_WEBHOOK_SECRET 則略過；有設定則比對標頭。"""
    expected = _webhook_secret_expected()
    if not expected:
        return True
    if headers is None:
        return False
    try:
        got = headers.get("X-Telegram-Bot-Api-Secret-Token")
        if got is None:
            got = headers.get("X-Telegram-Bot-Api-Secret-token")
    except AttributeError:
        return False
    return str(got or "").strip() == expected


def format_decision_message(summary: dict[str, Any]) -> str:
    rc = summary.get("reason_code")
    if rc is None or rc == "":
        tr0 = summary.get("trace")
        if isinstance(tr0, dict):
            rc = tr0.get("reason_code")
    rc_s = str(rc) if rc not in (None, "") else "UNKNOWN"

    tr = summary.get("trace") if isinstance(summary.get("trace"), dict) else {}
    ins = tr.get("inputs") if isinstance(tr.get("inputs"), dict) else {}

    branch = tr.get("branch")
    if branch is None:
        branch = summary.get("trace_branch")

    delta = ins.get("delta_strength")
    if delta is None:
        delta = summary.get("trace_delta_strength")

    ext = ins.get("extension_points")
    if ext is None:
        ext = summary.get("trace_extension_points")

    regime = summary.get("regime")
    if regime is None or str(regime).strip() == "":
        regime = ins.get("regime")
    if regime is None or str(regime).strip() == "":
        regime = tr.get("regime")

    lines = [
        "[Decision]",
        "",
        f"symbol: {summary.get('symbol')}",
        f"signal: {summary.get('signal')}",
        f"decision: {summary.get('decision')}",
        "",
        f"reason_code: {rc_s}",
        f"branch: {branch if branch is not None else 'NONE'}",
        "",
        f"delta_strength: {delta}",
        f"extension_points: {ext}",
        f"regime: {regime}",
    ]
    rid = summary.get("request_id")
    if rid:
        lines.extend(["", f"request_id: {rid}"])
    return "\n".join(lines)


def format_fill_message(summary: dict[str, Any]) -> str:
    applied = summary.get("applied")
    reason = summary.get("reason")
    dedupe = summary.get("dedupe")
    lines = [
        "[FillResult]",
        f"applied={applied}",
        f"reason={reason}",
        f"dedupe={dedupe}",
        f"request_id={summary.get('request_id')}",
        f"fill_id={summary.get('fill_id')}",
        f"pnl={summary.get('pnl')}",
        f"broker={summary.get('broker')}",
        f"broker_order_id={summary.get('broker_order_id')}",
        f"client_order_id={summary.get('client_order_id')}",
        f"state_save_error={summary.get('state_save_error')}",
    ]
    lifecycle = summary.get("lifecycle")
    if lifecycle is not None:
        lines.append(f"lifecycle={json.dumps(lifecycle, ensure_ascii=False, default=str)}")
    else:
        lines.append("lifecycle=None")
    return "\n".join(lines)


def format_state_message(state: dict[str, Any]) -> str:
    return (
        "[State]\n"
        f"trading_day={state.get('trading_day')}\n"
        f"regime={state.get('regime')}\n"
        f"today_realized_pnl={state.get('today_realized_pnl')}\n"
        f"today_loss={state.get('today_loss')}\n"
        f"consecutive_loss={state.get('consecutive_loss')}\n"
        f"cooldown_until={state.get('cooldown_until')}\n"
        f"daily_trade_count={state.get('daily_trade_count')}\n"
        f"last_decision={state.get('last_decision')}\n"
        f"last_decision_reason={state.get('last_decision_reason')}\n"
        f"last_signal_ts={state.get('last_signal_ts')}\n"
        f"lock_reason={state.get('lock_reason')}\n"
        f"updated_at={state.get('updated_at')}"
    )


def format_help_message() -> str:
    return (
        "[Help]\n"
        "指令：\n"
        "/state — 顯示目前交易狀態（檔案 state，會先做跨日 reset）\n"
        "/last — 顯示 signal_log 最後一筆 decision_result 與 fill_result\n"
        "/help — 顯示本說明\n"
        "決策通知：ENABLE_TELEGRAM_NOTIFY=true 且設定 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID 時走 Telegram；"
        "未設定或空字串時 stdout（mode=print）；明確 false 時 stdout（mode=disabled）。\n"
        "fill-result 通知仍沿用既有行為（stdout 或 Telegram，視開關與憑證）。"
    )


def format_last_message(
    *,
    signal_log_path: Path | None = None,
    max_lines: int = 4096,
) -> str:
    path = signal_log_path or SIGNAL_LOG_PATH
    dec = tail_jsonl_find_last(
        path,
        lambda o: o.get("event_type") == "decision_result",
        max_lines=max_lines,
    )
    fill = tail_jsonl_find_last(
        path,
        lambda o: o.get("event_type") == "fill_result",
        max_lines=max_lines,
    )
    parts = ["[Last]"]
    if dec:
        parts.append("Last decision_result:")
        parts.append(
            "\n".join(
                [
                    f"  request_id={dec.get('request_id')}",
                    f"  timestamp={dec.get('timestamp')}",
                    f"  decision={dec.get('decision')}",
                    f"  reason={dec.get('reason')}",
                    f"  command_write={json.dumps(dec.get('command_write'), ensure_ascii=False, default=str)}",
                ]
            )
        )
    else:
        parts.append("Last decision_result:（無資料）")
    parts.append("")
    if fill:
        parts.append("Last fill_result:")
        parts.append(
            "\n".join(
                [
                    f"  request_id={fill.get('request_id')}",
                    f"  timestamp={fill.get('timestamp')}",
                    f"  dedupe={fill.get('dedupe')}",
                    f"  state_save_error={fill.get('state_save_error')}",
                    f"  lifecycle={json.dumps(fill.get('lifecycle'), ensure_ascii=False, default=str)}",
                ]
            )
        )
    else:
        parts.append("Last fill_result:（無資料）")
    return "\n".join(parts)


def _send_message(
    text: str,
    *,
    chat_id: str | None = None,
    token: str | None = None,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    """
    呼叫 Telegram sendMessage。回傳結構化結果，不拋例外。
    鍵：ok, status_code, error
    """
    base: dict[str, Any] = {"ok": False, "status_code": None, "error": None}
    tok = (token or _env_token() or "").strip()
    cid = (chat_id or _env_chat_id() or "").strip()
    if not tok:
        base["error"] = "missing_token"
        return base
    if not cid:
        base["error"] = "missing_chat_id"
        return base
    url = f"{TELEGRAM_API}/bot{tok}/sendMessage"
    body = json.dumps(
        {
            "chat_id": cid,
            "text": text,
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    raw = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", None) or resp.getcode()
            base["status_code"] = int(status)
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(exc)
        base["status_code"] = int(exc.code)
        base["error"] = err_body[:2000]
        return base
    except Exception as exc:  # noqa: BLE001
        base["error"] = str(exc)[:2000]
        return base

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        base["error"] = f"invalid_json_response:{raw[:500]}"
        return base
    if not isinstance(data, dict):
        base["error"] = "unexpected_response_shape"
        return base
    if data.get("ok") is True:
        base["ok"] = True
        base["error"] = None
        return base
    base["error"] = str(data.get("description") or data)[:2000]
    return base


def notify_decision(summary: dict[str, Any]) -> dict[str, Any]:
    """
    決策通知：格式化後送出或 print；不拋例外。
    回傳：ok, status_code, error, mode（telegram | print | disabled | missing_credentials）。
    """
    msg = format_decision_message(summary)
    use_api, offline_mode = _telegram_decision_notify_mode()
    if not use_api:
        print(msg)
        return {"ok": True, "status_code": None, "error": None, "mode": offline_mode}

    tok = _env_token()
    cid = _env_chat_id()
    if not tok or not cid:
        print(f"[telegram-warning] missing token/chat_id; fallback print\n{msg}")
        return {
            "ok": True,
            "status_code": None,
            "error": None,
            "mode": "missing_credentials",
        }

    result = _send_message(msg)
    out: dict[str, Any] = {**result, "mode": "telegram"}
    if not result.get("ok"):
        print(f"[telegram-error] sendMessage failed: {result}\n{msg}")
    return out


def notify_fill_result(summary: dict[str, Any]) -> dict[str, Any]:
    """fill-result 通知（含 applied true/false、duplicate 路徑）。"""
    msg = format_fill_message(summary)
    if not _is_notify_enabled():
        print(msg)
        return {"ok": True, "status_code": None, "error": None, "mode": "printed"}

    tok = _env_token()
    cid = _env_chat_id()
    if not tok or not cid:
        print(f"[telegram-warning] missing token/chat_id; fallback print\n{msg}")
        return {
            "ok": True,
            "status_code": None,
            "error": None,
            "mode": "printed_missing_credentials",
        }

    result = _send_message(msg)
    if not result.get("ok"):
        print(f"[telegram-error] sendMessage failed: {result}\n{msg}")
    return result


def handle_telegram_update(update: dict[str, Any]) -> None:
    """處理單筆 Update：授權 chat 後回覆指令。"""
    fields = extract_update_fields(update)
    chat_id = fields.get("chat_id")
    text = fields.get("text") or ""
    if not _authorized_chat_id(chat_id):
        return

    cmd = (text or "").strip().split(maxsplit=1)[0].lower()
    if cmd in ("/help", "/start"):
        reply = format_help_message()
    elif cmd == "/state":
        st = reset_state_if_new_day(load_state())
        reply = format_state_message(st)
    elif cmd == "/last":
        reply = format_last_message()
    else:
        if not text:
            return
        reply = format_help_message()

    res = _send_message(reply, chat_id=str(chat_id) if chat_id is not None else None)
    if not res.get("ok"):
        print(f"[telegram-error] command reply failed: {res}")


def process_telegram_webhook(
    body: Any,
    headers: Any,
) -> tuple[dict[str, Any], int]:
    """
    HTTP 層薄轉發用：驗證 secret、解析 JSON、分派 handle_telegram_update。
    回傳 (json_dict, http_status)。
    """
    if not verify_telegram_webhook_secret(headers):
        return {"ok": False, "error": "invalid_webhook_secret"}, 403
    if not isinstance(body, dict):
        return {"ok": False, "error": "invalid_json"}, 400
    try:
        handle_telegram_update(body)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram-error] handle_telegram_update: {exc}")
        return {"ok": False, "error": "handler_exception"}, 500
    return {"ok": True}, 200
