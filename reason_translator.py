from __future__ import annotations

from typing import Any, Literal

Severity = Literal["info", "warning", "danger"]

_SEVERITIES: set[str] = {"info", "warning", "danger"}

_ALIASES: dict[str, str] = {
    "LOSS_STREAK_BLOCKED": "LOSS_STREAK_LIMIT",
    "DUPLICATE_IGNORED": "DUPLICATE_SIGNAL",
    "RISK_DAILY_LOSS_GUARD": "RISK_BLOCKED",
    "RISK_HIGH_VOL_TIGHTEN": "HIGH_VOL_GUARDRAIL",
    "HIGH_VOL_DOWNGRADE": "HIGH_VOL_GUARDRAIL",
}

_CATALOG: dict[str, dict[str, str]] = {
    "STATE_GATE": {
        "title": "State Gate 攔截",
        "message": "目前狀態閘門不允許交易，系統已略過本次訊號。",
        "severity": "warning",
        "suggested_action": "先檢查 cooldown、loss streak、session 與 daily lock 狀態。",
    },
    "COOLDOWN_ACTIVE": {
        "title": "冷卻中",
        "message": "系統處於冷卻時間內，暫不允許新交易。",
        "severity": "warning",
        "suggested_action": "等待冷卻結束後再重新評估訊號。",
    },
    "LOSS_STREAK_LIMIT": {
        "title": "連敗上限",
        "message": "連續虧損已達上限，系統暫停交易以保護資金。",
        "severity": "danger",
        "suggested_action": "降低風險或人工檢查策略，再決定是否解除限制。",
    },
    "DAILY_LOSS_LIMIT": {
        "title": "日虧損上限",
        "message": "已觸發單日虧損上限，今日不應再增加風險。",
        "severity": "danger",
        "suggested_action": "停止當日交易，檢查風控參數與交易紀錄。",
    },
    "DUPLICATE_SIGNAL": {
        "title": "重複訊號",
        "message": "收到短時間內相同訊號，為避免重複執行已略過。",
        "severity": "info",
        "suggested_action": "確認上游 webhook 是否重送，必要時調整 dedupe TTL。",
    },
    "SESSION_CLOSED": {
        "title": "非交易時段",
        "message": "目前不在允許交易的 session，系統不會執行交易。",
        "severity": "warning",
        "suggested_action": "確認交易時段設定與實際市場時區。",
    },
    "RISK_REDUCED": {
        "title": "風險已縮減",
        "message": "風控引擎已降低倉位或風險額度，採保守執行。",
        "severity": "warning",
        "suggested_action": "檢查波動、帳戶狀態與風險係數設定。",
    },
    "RISK_BLOCKED": {
        "title": "風控阻擋",
        "message": "風控規則判定不可開新倉，本次意圖已被阻擋。",
        "severity": "danger",
        "suggested_action": "檢查 max daily loss、帳戶限制與風險閾值設定。",
    },
    "HIGH_VOL_GUARDRAIL": {
        "title": "高波動護欄",
        "message": "市場高波動條件觸發保護，訊號被降級或限制。",
        "severity": "warning",
        "suggested_action": "等待波動回落，或改用更保守入場條件。",
    },
    "DECISION_SKIP": {
        "title": "策略略過",
        "message": "策略判定本次不交易（SKIP）。",
        "severity": "info",
        "suggested_action": "檢查 trace 與決策分支，確認是否符合預期規則。",
    },
    "UNKNOWN": {
        "title": "未知原因",
        "message": "無法識別的 reason_code，已使用預設說明。",
        "severity": "warning",
        "suggested_action": "檢查上游 reason_code 定義，並補齊翻譯對照。",
    },
}


def _normalize_reason_code(reason_code: Any) -> str:
    if reason_code is None:
        return "UNKNOWN"
    text = str(reason_code).strip().upper()
    return text or "UNKNOWN"


def _safe_details(details: dict | None) -> dict[str, Any]:
    if isinstance(details, dict):
        return details
    return {}


def _render_message(code: str, base_message: str, details: dict[str, Any]) -> str:
    if code == "COOLDOWN_ACTIVE":
        blocked_until = details.get("blocked_until") or details.get("cooldown_until")
        if blocked_until:
            return f"{base_message} 預計解鎖時間：{blocked_until}。"
    return base_message


def translate_reason_code(reason_code: str, details: dict | None = None) -> dict[str, str]:
    raw_reason_code = _normalize_reason_code(reason_code)
    canonical_code = _ALIASES.get(raw_reason_code, raw_reason_code)
    if canonical_code not in _CATALOG:
        canonical_code = "UNKNOWN"

    profile = _CATALOG[canonical_code]
    severity = profile["severity"] if profile["severity"] in _SEVERITIES else "warning"
    safe_details = _safe_details(details)
    message = _render_message(canonical_code, profile["message"], safe_details)

    return {
        "raw_reason_code": raw_reason_code,
        "reason_code": canonical_code,
        "title": profile["title"],
        "message": message,
        "severity": severity,
        "suggested_action": profile["suggested_action"],
    }

