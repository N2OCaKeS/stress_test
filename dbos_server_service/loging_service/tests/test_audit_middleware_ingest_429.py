"""`audit_access` middleware должен логировать 429 на ingest-путях.

До правки: success/4xx/5xx на `POST /events` и `POST /services/.../events`
скипались (anti-amplification), кроме 401/403. 429 (flood утёкшим
`SERVICE_API_KEY`) тоже скипался — SOC не видел всплеска, выпуск
журнала проходил молча.

Сейчас: 429 на ingest идёт в audit как `http.client_error`, симметрично
401/403.
"""

from __future__ import annotations

import time

from sqlalchemy import select

from src.models.audit_event import AuditEvent
from tests.conftest import make_event


EVENTS_URL = "/api/logging/v1/events"


def _wait_for_event(db, action: str, status_code: int, timeout: float = 1.5):
    """Опросом ждём, пока drain выгребет событие из outbox в БД."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.action == action)
        ).scalars().all()
        for r in rows:
            details = r.details or {}
            if details.get("status_code") == status_code:
                return r
        time.sleep(0.05)
    return None


def test_429_on_ingest_is_audited(client, auth_headers, db, TestSessionLocal, monkeypatch):
    """3-й POST /events при `INGEST_RATE_LIMIT=2/minute` отбивается 429
    и пишется в audit как `http.client_error` с `status_code=429`."""
    # drain выгружает audit-row через свою SessionLocal — патчим её на тест-
    # сессию, иначе drain ходит в дефолтный DATABASE_URL.
    import src.db.session as session_module
    monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

    monkeypatch.setenv("INGEST_RATE_LIMIT", "2/minute")
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.main import limiter
    limiter.reset()

    # Первые два запроса — 201.
    for i in range(2):
        r = client.post(EVENTS_URL, json=make_event(), headers=auth_headers)
        assert r.status_code == 201, f"request #{i} got {r.status_code}: {r.text}"

    # Третий — 429.
    r = client.post(EVENTS_URL, json=make_event(), headers=auth_headers)
    assert r.status_code == 429, r.text

    audited = _wait_for_event(db, "http.client_error", 429)
    assert audited is not None, "429 на POST /events должен попасть в audit-журнал"
    assert audited.details.get("path") == EVENTS_URL
    assert audited.details.get("method") == "POST"
