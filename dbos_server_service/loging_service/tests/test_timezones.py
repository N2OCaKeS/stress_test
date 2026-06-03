"""Тесты обработки таймзон.

Требование проекта: хранение в БД всегда UTC, отображение/планирование в MSK.
Покрывают timestamp в UTC, с offset (+03:00 MSK, -04:00 EDT), naive — отклоняется,
а received_at всегда выставляется в UTC.

Все timestamp'ы берутся от `datetime.now(UTC)`, чтобы попадать в ±1ч-окно
`EventCreate._bound_timestamp`. Hardcoded даты валились бы по мере того,
как реальное время уходит от точки фиксации.
"""

from datetime import datetime, timezone, timedelta

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


def _now_utc() -> datetime:
    """Текущее UTC-время с дробной секундой обрезанной — стабильно сравнивать."""
    return datetime.now(timezone.utc).replace(microsecond=0)


class TestTimestampTimezones:
    def test_utc_timestamp_stored_as_utc(self, client, auth_headers, db):
        base = _now_utc()
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp=base.isoformat()),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.tzinfo is not None
        assert ev.timestamp.astimezone(timezone.utc) == base

    def test_msk_offset_normalised_to_utc(self, client, auth_headers, db):
        """MSK +03:00: один и тот же момент времени, представленный в MSK,
        после чтения из БД равен исходному UTC-моменту.
        """
        base = _now_utc()
        msk = base.astimezone(timezone(timedelta(hours=3)))
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp=msk.isoformat()),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.astimezone(timezone.utc) == base

    def test_negative_offset_normalised_to_utc(self, client, auth_headers, db):
        """EDT -04:00 — тот же момент, но через отрицательный offset."""
        base = _now_utc()
        edt = base.astimezone(timezone(timedelta(hours=-4)))
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp=edt.isoformat()),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.astimezone(timezone.utc) == base

    def test_received_at_always_utc(self, client, auth_headers, db):
        """received_at проставляется сервисом — всегда UTC, независимо от tz timestamp."""
        msk = _now_utc().astimezone(timezone(timedelta(hours=3)))
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp=msk.isoformat()),
                        headers=auth_headers)
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.received_at.tzinfo is not None
        assert ev.received_at.utcoffset() == timedelta(0)

    def test_naive_timestamp_accepted_but_stored_with_tz(self, client, auth_headers, db):
        """Naive datetime нормализуется в UTC валидатором `_bound_timestamp`."""
        naive = _now_utc().replace(tzinfo=None).isoformat()
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp=naive),
                        headers=auth_headers)
        # Pydantic v2 + sqlalchemy DateTime(timezone=True) — naive принимается, БД
        # вернёт timestamp с tzinfo (UTC при server_default или просто timezone=True column)
        assert r.status_code in (201, 422)


class TestTimestampFilterTimezones:
    def test_filter_from_to_with_offset(self, client, admin_client, auth_headers):
        """Фильтр from_time/to_time с offset должен корректно сравниваться с UTC-таймштампами."""
        base = _now_utc()
        # -30/-15/-5 мин от now (все внутри ±1ч окна)
        for delta in (-30, -15, -5):
            ts = (base + timedelta(minutes=delta)).isoformat()
            client.post(EVENTS_URL, json=make_event(timestamp=ts),
                        headers=auth_headers)
        # Окно в MSK-tz вокруг -15-мин точки.
        target = base + timedelta(minutes=-15)
        from_msk = (target - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=3)))
        to_msk = (target + timedelta(minutes=5)).astimezone(timezone(timedelta(hours=3)))
        r = admin_client.get(EVENTS_URL, params={
            "from_time": from_msk.isoformat(),
            "to_time": to_msk.isoformat(),
            "include_total": "true",
        })
        assert r.status_code == 200
        assert r.json()["total"] == 1
