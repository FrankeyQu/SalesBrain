from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


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


def parse_weekday(value: str) -> int:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("invalid_weekday: empty")
    if text.isdigit():
        weekday = int(text)
        if 0 <= weekday <= 6:
            return weekday
        raise ValueError(f"invalid_weekday: {value!r}")
    if text in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[text]
    raise ValueError(f"invalid_weekday: {value!r}")


def parse_weekly_day_time(value: str) -> tuple[int, int, int]:
    text = str(value).strip()
    parts = text.split()
    if len(parts) != 2:
        raise ValueError(f"invalid_weekly_day_time: {value!r}")
    weekday = parse_weekday(parts[0])
    hour, minute = parse_hhmm(parts[1])
    return weekday, hour, minute


def next_weekly_run(now: datetime, weekly_day_time: str) -> datetime:
    weekday, hour, minute = parse_weekly_day_time(weekly_day_time)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0 and candidate <= now:
        days_ahead = 7
    candidate += timedelta(days=days_ahead)
    return candidate


def parse_iso_datetime(value: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty datetime")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
