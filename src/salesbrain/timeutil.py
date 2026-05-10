from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def zoneinfo_or_utc(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def now_in_zone(name: str) -> datetime:
    return datetime.now(zoneinfo_or_utc(name))


def iso_now(name: str) -> str:
    return now_in_zone(name).isoformat(timespec="seconds")


def today_iso(name: str) -> str:
    return now_in_zone(name).date().isoformat()


def yesterday_iso(name: str) -> str:
    return (now_in_zone(name) - timedelta(days=1)).date().isoformat()


def parse_hhmm(value: str) -> tuple[int, int]:
    text = str(value).strip()
    if not text or ":" not in text:
        raise ValueError(f"invalid_hhmm: {value!r}")
    hour_text, minute_text = text.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid_hhmm: {value!r}")
    return hour, minute


def next_daily_run(now: datetime, hhmm: str) -> datetime:
    hour, minute = parse_hhmm(hhmm)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_interval_run(now: datetime, minutes: int) -> datetime:
    if minutes <= 0:
        raise ValueError("interval minutes must be positive")
    return now + timedelta(minutes=minutes)


def parse_iso_datetime(value: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty datetime")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))

