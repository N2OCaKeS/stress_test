"""UTC datetime helpers."""

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def expires_at(*, minutes: int = 0, days: int = 0) -> datetime:
    return utcnow() + timedelta(minutes=minutes, days=days)


def is_expired(dt: datetime) -> bool:
    return dt.replace(tzinfo=timezone.utc) < utcnow()
