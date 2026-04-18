from __future__ import annotations

from datetime import datetime, timedelta, timezone

TAIPEI_TZ = timezone(timedelta(hours=8))


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def iso_now_taipei() -> str:
    return now_taipei().isoformat(timespec="seconds")


def today_str_taipei() -> str:
    return now_taipei().date().isoformat()


def parse_iso_dt(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TAIPEI_TZ)
    return dt.astimezone(TAIPEI_TZ)
