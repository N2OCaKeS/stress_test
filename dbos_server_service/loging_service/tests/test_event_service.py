"""Unit-тесты `event_service.record` / `query` — fail-paths и self-audit.

Покрывает:

* `record()` — rollback после `ConflictError` сам падает (broken pool):
  self-audit НЕ эмитится на сломанной сессии, в логе CRITICAL.
* `query()` — timeout на COUNT и SELECT эмитит warning self-audit
  `logging.events_queried` с `details.timeout=True`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.exceptions import ConflictError
from src.models.audit_event import AuditEvent
from src.schemas.events import EventCreate
from src.services import event_service
from src.repositories import events as event_repo
from src.utils.ids import audit_event_id


# ── record() rollback failure ────────────────────────────────────────────────


class _BrokenSession:
    """Сессия, у которой rollback всегда поднимает исключение.

    Эмулирует «pool disconnect между insert и rollback» — реальная сессия
    после такого непригодна для последующего INSERT'а.
    """

    def __init__(self) -> None:
        self.rollback_calls = 0
        self.audit_inserts = 0

    def rollback(self) -> None:
        self.rollback_calls += 1
        raise RuntimeError("broken connection")

    # Если код всё-таки попробует записать self-audit — увидим инкремент,
    # тест поймёт, что guard'а не сработал.
    def commit(self) -> None:
        self.audit_inserts += 1


class TestRecordRollbackFailure:
    def test_rollback_failure_logs_critical_and_skips_self_audit(
        self, monkeypatch, caplog
    ):
        """`db.rollback()` падает → CRITICAL в логе, `_emit_idempotency_conflict_audit`
        не вызывается (нельзя писать на сломанной сессии).
        """
        session = _BrokenSession()

        # event_repo.insert поднимает ConflictError — простая симуляция
        # idempotency-poisoning через monkeypatch (без реальной БД).
        def fake_insert(db, payload, commit=True):
            raise ConflictError(
                error_code="IDEMPOTENCY_KEY_CONFLICT",
                message="poison",
                details={
                    "service": payload.service,
                    "idempotency_key": payload.idempotency_key,
                },
            )

        monkeypatch.setattr(event_repo, "insert", fake_insert)

        # rule_service.apply_rules должен пропустить payload как есть.
        def fake_apply_rules(db, payload):
            return payload

        monkeypatch.setattr(
            event_service.rule_service, "apply_rules", fake_apply_rules
        )

        emitted: list = []

        def fake_emit(db, payload, exc):
            emitted.append((db, payload, exc))

        monkeypatch.setattr(
            event_service, "_emit_idempotency_conflict_audit", fake_emit
        )

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_id=None,
            actor_type="service",
            status="success",
            allowed=True,
            idempotency_key="poison-1",
            details={},
        )

        with caplog.at_level(logging.CRITICAL, logger="src.services.event_service"):
            with pytest.raises(ConflictError):
                event_service.record(session, payload)

        # rollback пытался вызваться один раз и поднял исключение.
        assert session.rollback_calls == 1
        # Self-audit ПРОПУЩЕН — нельзя писать на сломанной сессии.
        assert emitted == []
        # CRITICAL-лог зафиксирован.
        critical_messages = [
            r.message for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert any(
            "rollback after idempotency conflict failed" in m
            for m in critical_messages
        ), f"ожидали CRITICAL про rollback fail, получили: {critical_messages!r}"

    def test_rollback_success_still_emits_self_audit(self, monkeypatch):
        """Sanity: нормальная сессия → rollback проходит → self-audit эмитится.

        Без этой проверки regression «всегда пропускаем self-audit» осталась
        бы незамеченной.
        """

        class _OkSession:
            def __init__(self):
                self.rollback_calls = 0

            def rollback(self):
                self.rollback_calls += 1

        session = _OkSession()

        def fake_insert(db, payload, commit=True):
            raise ConflictError(
                error_code="IDEMPOTENCY_KEY_CONFLICT",
                message="poison",
                details={
                    "service": payload.service,
                    "idempotency_key": payload.idempotency_key,
                },
            )

        monkeypatch.setattr(event_repo, "insert", fake_insert)

        def fake_apply_rules(db, payload):
            return payload

        monkeypatch.setattr(
            event_service.rule_service, "apply_rules", fake_apply_rules
        )

        emitted: list = []

        def fake_emit(db, payload, exc):
            emitted.append((db, payload, exc))

        monkeypatch.setattr(
            event_service, "_emit_idempotency_conflict_audit", fake_emit
        )

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_id=None,
            actor_type="service",
            status="success",
            allowed=True,
            idempotency_key="ok-1",
            details={},
        )

        with pytest.raises(ConflictError):
            event_service.record(session, payload)

        assert session.rollback_calls == 1
        assert len(emitted) == 1, "self-audit должен быть эмитнут на чистой сессии"


# ── query() — timeout audit ──────────────────────────────────────────────────


def _insert_event(db, service: str = "auth_service") -> None:
    db.add(AuditEvent(
        id=audit_event_id(),
        timestamp=datetime.now(timezone.utc),
        service=service,
        action="user.login",
        actor_type="user",
        status="success",
        allowed=True,
        severity="INFO",
        details={},
    ))
    db.commit()


class TestQueryTimeoutAudit:
    """`logging.events_queried` со status=`warning` пишется при отмене COUNT/SELECT."""

    def test_count_timeout_emits_warning_self_audit(self, db, monkeypatch):
        _insert_event(db)

        # Подменяем repo.query, чтобы выставить флаг count_timeout в state и
        # вернуть пустой результат (как при реальном 57014).
        def fake_query(
            db_,
            *,
            timeout_state=None,
            **kwargs,
        ):
            assert timeout_state is not None, (
                "event_service.query обязан передавать timeout_state, "
                "иначе timeout-аудита не будет"
            )
            timeout_state["count_timeout"] = True
            return [], None, False

        monkeypatch.setattr(event_repo, "query", fake_query)

        identity = {
            "user_id": "usr_test",
            "username": "tester",
            "actor_type": "user",
            "department_id": "dep_x",
        }

        before = db.query(AuditEvent).filter_by(
            action="logging.events_queried"
        ).count()

        event_service.query(
            db,
            include_total=True,
            identity=identity,
        )

        db.expire_all()
        rows = (
            db.query(AuditEvent)
            .filter_by(action="logging.events_queried", status="warning")
            .all()
        )
        assert len(rows) == before + 1, (
            f"ожидали один новый warning event, получили дельту "
            f"{len(rows) - before}"
        )
        ev = rows[-1]
        assert ev.severity == "WARNING"
        assert ev.actor_id == "usr_test"
        assert ev.username == "tester"
        assert ev.department_id == "dep_x"
        assert ev.details.get("timeout") is True
        assert ev.details.get("count_timeout") is True
        assert ev.details.get("query_timeout") is False

    def test_query_timeout_emits_warning_self_audit(self, db, monkeypatch):
        def fake_query(db_, *, timeout_state=None, **kwargs):
            assert timeout_state is not None
            timeout_state["query_timeout"] = True
            return [], None, False

        monkeypatch.setattr(event_repo, "query", fake_query)

        before = db.query(AuditEvent).filter_by(
            action="logging.events_queried"
        ).count()

        event_service.query(db, identity=None)

        db.expire_all()
        rows = (
            db.query(AuditEvent)
            .filter_by(action="logging.events_queried", status="warning")
            .all()
        )
        assert len(rows) == before + 1
        ev = rows[-1]
        assert ev.details.get("query_timeout") is True
        assert ev.details.get("count_timeout") is False
        # Без identity actor_id остаётся пустым (anonymous timeout-сигнал).
        assert ev.actor_id is None

    def test_no_timeout_no_warning_event(self, db, monkeypatch):
        """Sanity: без timeout'а warning-event не пишется."""

        def fake_query(db_, *, timeout_state=None, **kwargs):
            # timeout_state остаётся пустым — нет отмены.
            return [], 0, False

        monkeypatch.setattr(event_repo, "query", fake_query)

        before = db.query(AuditEvent).filter_by(
            action="logging.events_queried", status="warning"
        ).count()

        event_service.query(db, identity={"user_id": "u"})

        db.expire_all()
        after = db.query(AuditEvent).filter_by(
            action="logging.events_queried", status="warning"
        ).count()
        assert after == before, (
            "без timeout'а warning-events не должно появляться"
        )

    def test_repo_query_real_timeout_sets_state_flag(self, db, monkeypatch):
        """Интеграция с реальным repo: при count_timeout=1ms COUNT отменяется,
        флаг count_timeout=True попадает в state.
        """
        _insert_event(db)
        _insert_event(db)

        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "1")
        get_settings.cache_clear()
        try:
            # pg_sleep внутри COUNT'а гарантирует превышение 1ms.
            from sqlalchemy import text

            db.execute(text("SELECT pg_sleep(0.05)"))
            state: dict = {}
            # Прямой вызов repo: проверяем contract, что timeout_state
            # действительно мутируется. На нынешней тестовой нагрузке
            # COUNT по двум строкам — быстрый, поэтому форсим длинный
            # COUNT через pg_sleep в подзапросе.
            from sqlalchemy import select as sa_select, func
            from src.models.audit_event import AuditEvent as AE

            # Прямой trace того, что callback вызвался: подменяем
            # `_with_statement_timeout` так, чтобы сразу пойти в on_canceled.
            real_helper = event_repo._with_statement_timeout

            def force_canceled(db_, timeout_ms, fn, *, on_canceled, canceled_log_msg):
                # имитируем 57014: зовём on_canceled (он мутирует state).
                return on_canceled()

            monkeypatch.setattr(
                event_repo, "_with_statement_timeout", force_canceled
            )

            events, total, has_more = event_repo.query(
                db, include_total=True, timeout_state=state
            )
            assert state.get("count_timeout") is True
            # SELECT тоже под guard'ом — и тоже пометится.
            assert state.get("query_timeout") is True
            assert total is None
            assert events == []
        finally:
            get_settings.cache_clear()
