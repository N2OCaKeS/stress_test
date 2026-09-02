"""`EventCreate.timestamp` дрифт-окно зависит от `actor_type`.

`_bound_timestamp` отбивает backdating (атакующий с `SERVICE_API_KEY` мог бы
прислать event «год назад» — retention уничтожил бы трассу инцидента) и
forward-dating (event «в будущем» уехал бы за `to_time` SOC'а).

User/bot/anonymous/oauth_client — ±1ч (UI-flow, сессии короче часа).
Service — ±24ч (outbox-retry после длительного outage). Naive datetime
трактуется как UTC.

Тесты в этом файле работают с дефолтом `actor_type="user"`; per-actor
поведение покрыто в `test_payload_validation.py::TestTimestampActorTypeSkew`.
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

    def test_2h_future_rejected(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=2)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts))
        err = ei.value.errors()[0]
        assert err["type"] == "value_error"

    def test_year_past_rejected(self):
        """Классический backdating-приём: «событие год назад»."""
        ts = datetime.now(timezone.utc) - timedelta(days=365)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts))
        err = ei.value.errors()[0]
        assert err["type"] == "value_error"

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


class TestTimestampActorTypeSkew:
    """`actor_type` определяет окно дрифта `timestamp`.

    * service → ±24ч (outbox-retry допустим)
    * user/bot/anonymous/oauth_client → ±1ч (UI-flow)
    """

    def test_service_caller_12h_past_accepted(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=12)
        ev = EventCreate(**_payload(timestamp=ts, actor_type="service"))
        assert ev.timestamp == ts

    def test_service_caller_12h_future_accepted(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=12)
        ev = EventCreate(**_payload(timestamp=ts, actor_type="service"))
        assert ev.timestamp == ts

    def test_service_caller_25h_past_rejected(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=25)
        with pytest.raises(ValidationError) as ei:
            EventCreate(**_payload(timestamp=ts, actor_type="service"))
        # model_validator пишет ошибку на уровне модели — `loc` пустой или ()
        # для root-level ошибки. Достаточно проверить, что валидация падает
        # и причина именно value_error (а не type_error на каком-то поле).
        assert any(
            err["type"] == "value_error" for err in ei.value.errors()
        )

    def test_service_caller_25h_future_rejected(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=25)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts, actor_type="service"))

    def test_user_caller_2h_past_rejected_with_default_window(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=2)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts, actor_type="user"))

    def test_user_caller_30min_past_accepted(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=30)
        ev = EventCreate(**_payload(timestamp=ts, actor_type="user"))
        assert ev.timestamp == ts

    def test_bot_caller_uses_user_window(self):
        """bot — UI-flow class, окно ±1ч."""
        ts = datetime.now(timezone.utc) - timedelta(hours=12)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts, actor_type="bot"))

    def test_oauth_client_caller_uses_user_window(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=12)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts, actor_type="oauth_client"))

    def test_anonymous_caller_uses_user_window(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=12)
        with pytest.raises(ValidationError):
            EventCreate(**_payload(timestamp=ts, actor_type="anonymous"))
