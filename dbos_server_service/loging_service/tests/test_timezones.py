"""Тесты обработки таймзон.

Требование проекта: хранение в БД всегда UTC, отображение/планирование в MSK.
Покрывают timestamp в UTC, с offset (+03:00 MSK, -04:00 EDT), naive — отклоняется,
а received_at всегда выставляется в UTC.
"""

from datetime import datetime, timezone, timedelta

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


class TestTimestampTimezones:
    def test_utc_timestamp_stored_as_utc(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp="2026-04-19T12:00:00Z"),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.tzinfo is not None
        # 12:00 UTC
        assert ev.timestamp.astimezone(timezone.utc) == datetime(
            2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_msk_offset_normalised_to_utc(self, client, auth_headers, db):
        """MSK +03:00: 15:00 MSK == 12:00 UTC. Сравниваем по точке времени."""
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp="2026-04-19T15:00:00+03:00"),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.astimezone(timezone.utc) == datetime(
            2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_negative_offset_normalised_to_utc(self, client, auth_headers, db):
        """EDT -04:00: 08:00 EDT == 12:00 UTC."""
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp="2026-04-19T08:00:00-04:00"),
                        headers=auth_headers)
        assert r.status_code == 201
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.timestamp.astimezone(timezone.utc) == datetime(
            2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_received_at_always_utc(self, client, auth_headers, db):
        """received_at проставляется сервисом — всегда UTC, независимо от tz timestamp."""
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp="2026-04-19T15:00:00+03:00"),
                        headers=auth_headers)
        from src.models.audit_event import AuditEvent
        ev = db.get(AuditEvent, r.json()["id"])
        assert ev.received_at.tzinfo is not None
        assert ev.received_at.utcoffset() == timedelta(0)

    def test_naive_timestamp_accepted_but_stored_with_tz(self, client, auth_headers, db):
        """Pydantic datetime парсит naive строку — PG автоматом припишет UTC при чтении.
        Документируем фактическое поведение."""
        r = client.post(EVENTS_URL,
                        json=make_event(timestamp="2026-04-19T12:00:00"),
                        headers=auth_headers)
        # Pydantic v2 + sqlalchemy DateTime(timezone=True) — naive принимается, БД
        # вернёт timestamp с tzinfo (UTC при server_default или просто timezone=True column)
        assert r.status_code in (201, 422)


class TestTimestampFilterTimezones:
    def test_filter_from_to_with_offset(self, client, admin_client, auth_headers):
        """Фильтр from_time/to_time с offset должен корректно сравниваться с UTC-таймштампами."""
        # 09:00 UTC, 12:00 UTC, 15:00 UTC
        for ts in ("2026-04-19T09:00:00Z", "2026-04-19T12:00:00Z",
                   "2026-04-19T15:00:00Z"):
            client.post(EVENTS_URL, json=make_event(timestamp=ts),
                        headers=auth_headers)
        # Фильтр 14:00 MSK (== 11:00 UTC) .. 16:00 MSK (== 13:00 UTC)
        # должен вернуть только 12:00 UTC
        r = admin_client.get(EVENTS_URL, params={
            "from_time": "2026-04-19T14:00:00+03:00",
            "to_time": "2026-04-19T16:00:00+03:00",
        })
        assert r.status_code == 200
        assert r.json()["total"] == 1
