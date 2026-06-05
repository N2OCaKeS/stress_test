"""Регрессия: fallback-ветка `AuditOutbox.push_nowait` коммитит запись в БД.

Когда lifespan не поднимался (`_queue is None`) и push идёт в синхронный
fallback, прошлая версия звала `_writer(db, envelope)` (тот по дефолту —
`write_envelope_to_db`, который сам делает `record_admin_action(commit=False)`)
и сразу закрывала сессию без `db.commit()`. SQLAlchemy откатывал транзакцию
на `close()` → row не сохранялся, self-audit терялся молча.

Тест ставит реальный writer (`write_envelope_to_db`) на TEST_DATABASE_URL и
проверяет, что после `push_nowait` без `start()` строка лежит в `audit_events`.
"""

from src.services.audit_outbox import (
    AuditOutbox,
    make_envelope,
    write_envelope_to_db,
)
from src.models.audit_event import AuditEvent


class TestFallbackPersistsRow:
    def test_push_without_start_persists_to_db(self, TestSessionLocal, db):
        """После fallback-push строка существует в БД через независимую сессию."""
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=0.1,
            session_factory=TestSessionLocal,
            writer=write_envelope_to_db,
            bump_failure=bump,
        )
        envelope = make_envelope(
            action="user.login",
            actor_id="usr_test_fb",
            actor_type="user",
            username="fallback_user",
            emit_status="success",
            allowed=True,
            request_id="req_fb_1",
            details={"src": "fallback-test"},
        )

        # `start` НЕ зовём — `_queue is None`, push идёт в синхронный fallback.
        # Успешная запись через fallback возвращает True и попадает в drained.
        accepted = outbox.push_nowait(envelope)
        assert accepted is True
        assert failures["n"] == 0
        assert outbox.drained_total() == 1

        # Читаем независимой сессией: если коммит не сработал, INSERT откатился
        # бы на close() и здесь было бы пусто.
        verify = TestSessionLocal()
        try:
            rows = (
                verify.query(AuditEvent)
                .filter(AuditEvent.request_id == "req_fb_1")
                .all()
            )
        finally:
            verify.close()

        assert len(rows) == 1
        row = rows[0]
        assert row.action == "user.login"
        assert row.actor_id == "usr_test_fb"
        assert row.service == "loging_service"

    def test_push_without_start_writer_failure_rollback(self, TestSessionLocal, db):
        """Битый writer в fallback бампит failure-counter и не оставляет грязь."""
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        def bad_writer(session, env):
            # Эмулируем падение уже после INSERT'а в pending: должен сработать
            # rollback в fallback-ветке, чтобы close() не оставил висящую тx.
            from src.schemas.events import EventCreate
            from src.services.event_service import record_admin_action
            record_admin_action(
                session,
                EventCreate(
                    timestamp=env.enqueued_at,
                    service="loging_service",
                    action=env.action,
                    actor_id=env.actor_id,
                    actor_type="user",
                    username=env.username,
                    status=env.emit_status,
                    allowed=env.allowed,
                    request_id=env.request_id,
                    details=env.details,
                ),
                commit=False,
            )
            raise RuntimeError("writer boom")

        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=0.1,
            session_factory=TestSessionLocal,
            writer=bad_writer,
            bump_failure=bump,
        )
        envelope = make_envelope(
            action="user.login",
            actor_id="usr_test_fb2",
            actor_type="user",
            username="fallback_user_bad",
            emit_status="failure",
            allowed=False,
            request_id="req_fb_2",
            details={},
        )

        # Битый writer: fallback закрылся rollback'ом, событие не записано —
        # accepted=False, failure counter инкрементнут.
        accepted = outbox.push_nowait(envelope)
        assert accepted is False
        assert failures["n"] == 1
        assert outbox.drained_total() == 0

        verify = TestSessionLocal()
        try:
            rows = (
                verify.query(AuditEvent)
                .filter(AuditEvent.request_id == "req_fb_2")
                .all()
            )
        finally:
            verify.close()
        assert rows == []
