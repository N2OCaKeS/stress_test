"""UTC datetime-хелперы. Внутри сервиса всегда работаем в UTC, MSK — только на отображении."""

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Текущее время в UTC (aware datetime). Замена устаревшего `datetime.utcnow()`."""
    return datetime.now(timezone.utc)


def expires_at(*, minutes: int = 0, days: int = 0) -> datetime:
    """`now + timedelta(minutes=..., days=...)` — для TTL-полей."""
    return utcnow() + timedelta(minutes=minutes, days=days)


def is_expired(dt: datetime) -> bool:
    """True если `dt` уже в прошлом. Naive datetime считаем UTC."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware < utcnow()
