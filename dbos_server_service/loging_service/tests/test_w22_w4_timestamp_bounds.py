"""W22-W4 P3: `EventCreate.timestamp` ±1ч от `datetime.now(UTC)`.

`_bound_timestamp` отбивает backdating (атакующий с `SERVICE_API_KEY` мог бы
прислать event «год назад» — retention уничтожил бы трассу инцидента) и
forward-dating (event «в будущем» уехал бы за `to_time` SOC'а).

Окно ровно ±1ч. Naive datetime трактуется как UTC.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.schemas.events import EventCreate


def _payload(**kwargs) -> dict:
    base = {
        "timestamp": datetime.now(timezone.utc),
        "service": "auth_service",
        "action": "user.login",
        "status": "success",
        "allowed": True,
        "actor_type": "user",
    }
    base.update(kwargs)
    return base


class TestTimestampBoundsInsideWindow:
    def test_now_accepted(self):
        ts = datetime.now(timezone.utc)
        ev = EventCreate(**_payload(timestamp=ts))
        assert ev.timestamp == ts
        assert ev.timestamp.tzinfo == timezone.utc

    def test_30min_past_accepted(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=30)
        ev = EventCreate(**_payload(timestamp=ts))
        # tz сохраняется; точное значение проходит насквозь.
        assert ev.timestamp == ts
        assert ev.timestamp.tzinfo == timezone.utc

    def test_30min_future_accepted(self):
        ts = datetime.now(timezone.utc) + timedelta(minutes=30)
        ev = EventCreate(**_payload(timestamp=ts))
        assert ev.timestamp == ts
        assert ev.timestamp.tzinfo == timezone.utc

    def test_at_boundary_minus_50min_accepted(self):
        """Граница окна — 1ч; -50мин в зоне «комфортного запаса»."""
        ts = datetime.now(timezone.utc) - timedelta(minutes=50)
        ev = EventCreate(**_payload(timestamp=ts))
        assert ev.timestamp == ts

    def test_at_boundary_plus_50min_accepted(self):
        ts = datetime.now(timezone.utc) + timedelta(minutes=50)
        ev = EventCreate(**_payload(timestamp=ts))
        assert ev.timestamp == ts


class TestTimestampBoundsOutsideWindow:
    def test_2h_past_rejected(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=2)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts))
        err = ei.value.errors()[0]
        assert err["type"] == "value_error"
        assert err["loc"] == ("timestamp",)

    def test_2h_future_rejected(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=2)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts))
        err = ei.value.errors()[0]
        assert err["type"] == "value_error"
        assert err["loc"] == ("timestamp",)

    def test_year_past_rejected(self):
        """Классический backdating-приём: «событие год назад»."""
        ts = datetime.now(timezone.utc) - timedelta(days=365)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts))
        err = ei.value.errors()[0]
        assert err["type"] == "value_error"
        assert err["loc"] == ("timestamp",)

    def test_year_future_rejected(self):
        ts = datetime.now(timezone.utc) + timedelta(days=365)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts))

    def test_iso_string_too_old_rejected(self):
        """JSON-payload через FastAPI: timestamp приходит ISO-строкой."""
        ts_str = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts_str))


class TestTimestampNaiveDatetime:
    def test_naive_normalized_as_utc_and_validated(self):
        """Naive datetime → trace как UTC, и тогда проходит bound check."""
        naive = datetime.now(timezone.utc).replace(tzinfo=None)
        ev = EventCreate(**_payload(timestamp=naive))
        # Поле нормализовалось — после валидатора у timestamp есть tz.
        assert ev.timestamp.tzinfo is not None

    def test_naive_old_still_rejected(self):
        """Naive datetime в прошлом > 1ч — отбивается."""
        naive_old = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=naive_old))


class TestTimestampTimezoneOffsets:
    def test_msk_offset_inside_window(self):
        """Тот же момент в MSK (+03:00) — проходит."""
        msk = datetime.now(timezone(timedelta(hours=3)))
        EventCreate(**_payload(timestamp=msk))

    def test_negative_offset_inside_window(self):
        """Тот же момент в EDT (-04:00) — проходит."""
        edt = datetime.now(timezone(timedelta(hours=-4)))
        EventCreate(**_payload(timestamp=edt))
