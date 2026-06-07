"""Idempotency poisoning защита + graceful-shutdown dedup.

Покрывает два сценария:

1. **Poisoning**. Атакующий с `SERVICE_API_KEY` пытается заранее «застолбить»
   `(service, idempotency_key)` фейковым payload'ом. Когда легитимный
   сервис ретрайит свой настоящий event с тем же ключом, репозиторий
   должен отличить два случая:
   - **same payload** (hash совпал) → 201, существующий row (idempotent
     replay; outbox-retry safe);
   - **different payload** (hash разошёлся) → 409 IDEMPOTENCY_KEY_CONFLICT
     и WARNING self-audit (atacker rebuilds attribution trail).

2. **Graceful-shutdown dedup**. `_drain_remaining` после network-drop'а на
   `db.commit()` может запустить тот же envelope второй раз. Поскольку
   `make_envelope` теперь всегда генерит `idempotency_key`, второй INSERT
   падает в `ON CONFLICT DO NOTHING` + hash-match → один row в БД.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.audit_event import AuditEvent
from src.schemas.events import EventCreate
from src.services import event_service
from src.services.audit_outbox import AuditEnvelope, make_envelope, write_envelope_to_db
from src.repositories import events as event_repo
from src.core.exceptions import ConflictError
from tests.conftest import make_event


# ── idempotent replay: один key, тот же payload ───────────────────────────────


class TestIdempotentReplaySamePayload:
    def test_two_posts_same_key_same_payload_one_row(self, client, auth_headers, db):
        """Два POST с одним idempotency_key и идентичным payload → 201, одна row."""
        payload = make_event(idempotency_key="evt-abc-001")
        r1 = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert r1.status_code == 201, r1.text
        body1 = r1.json()

        r2 = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert r2.status_code == 201, r2.text
        body2 = r2.json()

        # Второй вызов вернул тот же id и received_at — outbox retry получает
        # каноническую запись, без дубля в журнале.
        assert body2["id"] == body1["id"]
        assert body2["received_at"] == body1["received_at"]

        count = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "auth_service",
                AuditEvent.idempotency_key == "evt-abc-001",
            )
        ).all()
        assert len(count) == 1


# ── poisoning: один key, разный payload ───────────────────────────────────────


class TestIdempotencyPoisoningDifferentPayload:
    def test_second_post_with_different_payload_returns_409(
        self, client, auth_headers, db
    ):
        """Второй POST с тем же key и расходящимся payload → 409, audit warning."""
        first = make_event(idempotency_key="evt-poison-1", action="user.login")
        r1 = client.post("/api/logging/v1/events", json=first, headers=auth_headers)
        assert r1.status_code == 201, r1.text

        # Тот же idempotency_key, но другое action → poisoning attempt.
        second = make_event(idempotency_key="evt-poison-1", action="user.logout")
        r2 = client.post("/api/logging/v1/events", json=second, headers=auth_headers)
        assert r2.status_code == 409, r2.text
        body = r2.json()
        assert body["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"

        # WARNING self-audit под service="loging_service" должен подняться,
        # чтобы SOC увидел факт расхождения. Self-audit пишется отдельной
        # транзакцией.
        warnings = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "loging_service",
                AuditEvent.action == "audit.idempotency_conflict",
            )
        ).scalars().all()
        assert len(warnings) == 1
        assert warnings[0].severity == "WARNING"
        # Detail-поля должны указать на исходный ключ — без них SIEM не
        # отследит атрибуцию.
        assert warnings[0].details["idempotency_key"] == "evt-poison-1"
        assert warnings[0].details["claimed_service"] == "auth_service"

    def test_poisoning_does_not_overwrite_existing_row(
        self, client, auth_headers, db
    ):
        """Conflict не должен молча подменить уже сохранённый event."""
        first = make_event(
            idempotency_key="evt-immutable-1",
            action="user.login",
            details={"ip": "1.2.3.4"},
        )
        r1 = client.post("/api/logging/v1/events", json=first, headers=auth_headers)
        assert r1.status_code == 201, r1.text
        original_id = r1.json()["id"]

        attack = make_event(
            idempotency_key="evt-immutable-1",
            action="user.logout",
            details={"ip": "9.9.9.9"},
        )
        client.post("/api/logging/v1/events", json=attack, headers=auth_headers)

        # Достаём оригинальный row — он должен остаться нетронутым.
        stored = db.execute(
            select(AuditEvent).where(AuditEvent.id == original_id)
        ).scalar_one()
        assert stored.action == "user.login"
        assert stored.details == {"ip": "1.2.3.4"}


# ── self-audit envelope всегда несёт idempotency_key ──────────────────────────


class TestIdempotencyKeyRequiredForCritical:
    def test_make_envelope_generates_key_when_not_provided(self):
        """`make_envelope` без явного key даёт уникальный UUID на каждый вызов."""
        env1 = make_envelope(
            action="user.ban",
            actor_id="usr_admin",
            actor_type="user",
            username="admin",
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )
        env2 = make_envelope(
            action="user.ban",
            actor_id="usr_admin",
            actor_type="user",
            username="admin",
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
        )
        assert env1.idempotency_key
        assert env2.idempotency_key
        assert env1.idempotency_key != env2.idempotency_key
        # UUID4-hex — 32 hex char'а.
        assert len(env1.idempotency_key) == 32

    def test_make_envelope_respects_explicit_key(self):
        env = make_envelope(
            action="user.ban",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
            idempotency_key="custom-key-1",
        )
        assert env.idempotency_key == "custom-key-1"

    def test_write_envelope_to_db_propagates_idempotency_key(self, db: Session):
        """write_envelope_to_db должен прокидывать key в EventCreate / row."""
        env = make_envelope(
            action="user.ban",
            actor_id="usr_admin",
            actor_type="user",
            username="admin",
            emit_status="success",
            allowed=True,
            request_id=None,
            details={"reason": "policy violation"},
            idempotency_key="ban-evt-1",
        )
        write_envelope_to_db(db, env)
        db.commit()

        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "loging_service",
                AuditEvent.idempotency_key == "ban-evt-1",
            )
        ).scalar_one()
        assert row.action == "user.ban"


# ── graceful-shutdown: повторный drain не дублирует ───────────────────────────


class TestGracefulShutdownNoDuplicates:
    def test_double_drain_of_same_envelope_yields_one_row(self, db: Session):
        """Двойной запуск `write_envelope_to_db` на одном envelope → одна row.

        Эмулирует сценарий graceful-shutdown'а: `_drain_remaining` уже
        попытался записать envelope, `db.commit()` упал (network drop),
        envelope ретраится → второй INSERT должен схлопнуться на partial
        UNIQUE через `ON CONFLICT DO NOTHING` (тот же idempotency_key,
        тот же payload-hash).
        """
        env = make_envelope(
            action="http.access_granted",
            actor_id="usr_x",
            actor_type="user",
            username="x",
            emit_status="success",
            allowed=True,
            request_id="req-1",
            details={"method": "GET", "path": "/x"},
            idempotency_key="dup-test-1",
        )

        write_envelope_to_db(db, env)
        db.commit()
        write_envelope_to_db(db, env)
        db.commit()

        rows = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "loging_service",
                AuditEvent.idempotency_key == "dup-test-1",
            )
        ).scalars().all()
        assert len(rows) == 1


# ── repository-уровень: hash sanity ───────────────────────────────────────────


class TestPayloadHashContract:
    def test_repo_insert_stores_payload_hash(self, db: Session):
        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.acquire",
            actor_id="usr_1",
            actor_type="user",
            department_id="dep_a",
            status="success",
            allowed=True,
            severity="INFO",
            idempotency_key="repo-h1",
            details={"server_id": "srv_1"},
        )
        row = event_repo.insert(db, payload)
        assert row.idempotency_payload_hash is not None
        assert len(row.idempotency_payload_hash) == 64  # SHA-256 hex

    def test_repo_insert_no_hash_when_no_key(self, db: Session):
        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.acquire",
            status="success",
            allowed=True,
            severity="INFO",
            details={},
        )
        row = event_repo.insert(db, payload)
        # Legacy path не использует hash вообще — колонка остаётся NULL.
        assert row.idempotency_payload_hash is None

    def test_repo_insert_raises_conflict_on_hash_mismatch(self, db: Session):
        first = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.acquire",
            status="success",
            allowed=True,
            severity="INFO",
            idempotency_key="repo-conflict-1",
            details={"server_id": "srv_1"},
        )
        event_repo.insert(db, first)

        second = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.release",  # отличие
            status="success",
            allowed=True,
            severity="INFO",
            idempotency_key="repo-conflict-1",
            details={"server_id": "srv_1"},
        )
        with pytest.raises(ConflictError) as ei:
            event_repo.insert(db, second)
        assert ei.value.error_code == "IDEMPOTENCY_KEY_CONFLICT"

    def test_idempotency_conflict_severity_comes_from_default_table(
        self, db: Session
    ):
        """`_emit_idempotency_conflict_audit` подтягивает severity из
        `_DEFAULT_SEVERITY`, а не хардкодит "WARNING" в payload'е. Поднимем
        запись в таблице до CRITICAL и убедимся, что эмитнутое событие
        получает именно её.
        """
        from src.services import event_service as evt_svc
        from src.services import rule_service

        original = rule_service._DEFAULT_SEVERITY[
            ("audit.idempotency_conflict", "warning")
        ]
        rule_service._DEFAULT_SEVERITY[
            ("audit.idempotency_conflict", "warning")
        ] = "CRITICAL"
        try:
            poison_payload = EventCreate(
                timestamp=datetime.now(timezone.utc),
                service="auth_service",
                action="user.login",
                status="success",
                allowed=True,
                severity="INFO",
                idempotency_key="evt-sev-table-1",
                details={"ip": "1.1.1.1"},
            )
            exc = ConflictError(
                error_code="IDEMPOTENCY_KEY_CONFLICT",
                message="payload hash mismatch",
            )
            evt_svc._emit_idempotency_conflict_audit(db, poison_payload, exc)
            rows = db.execute(
                select(AuditEvent).where(
                    AuditEvent.service == "loging_service",
                    AuditEvent.action == "audit.idempotency_conflict",
                    AuditEvent.details["idempotency_key"].astext == "evt-sev-table-1",
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].severity == "CRITICAL"
        finally:
            rule_service._DEFAULT_SEVERITY[
                ("audit.idempotency_conflict", "warning")
            ] = original

    def test_repo_insert_same_payload_returns_existing(self, db: Session):
        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.acquire",
            status="success",
            allowed=True,
            severity="INFO",
            idempotency_key="repo-replay-1",
            details={"server_id": "srv_1"},
        )
        first = event_repo.insert(db, payload)
        second = event_repo.insert(db, payload)
        assert first.id == second.id
        assert first.received_at == second.received_at
