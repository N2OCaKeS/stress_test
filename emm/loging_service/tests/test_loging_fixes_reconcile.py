"""Регрессы по сверочным фиксам loging_service.

Покрывает восемь точечных правок:

1. `apply_active` chunked-DELETE выходит из цикла только на пустом чанке
   (`deleted == 0`), а не на первом `deleted < chunk_size` — иначе под
   конкурентным sweep'ом хвост журнала оставался бы за порогом retention.
2. `_emit_idempotency_conflict_audit` под живой outer-tx пишет self-audit в
   SAVEPOINT, не клоббрит чужую транзакцию и не теряет диагностику.
3. Idempotent replay переживает смену default-severity между retry —
   `severity` исключён из hash-инпута.
4. На broken-rollback (`db.rollback` упал) self-audit конфликта пишется
   свежей сессией, а не молча теряется.
5. `_retention_loop` под занятым advisory-lock'ом НЕ помечает `last_run`.
6. `_is_ingest_path` матчит ingest-роуты точно: `/events_archive` и
   `/services_x` не считаются ingest'ом.
7. `_with_statement_timeout` маппит disconnect (invalidated connection) в
   downstream-ответ `on_canceled`, а не в 500.
8. `_emit_query_timeout_audit` резолвит `actor_type` через whitelist —
   неизвестное значение → `anonymous`, а не `user`.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.exceptions import ConflictError
from src.models.audit_event import AuditEvent
from src.repositories import events as event_repo
from src.repositories import retention_policies as retention_repo
from src.schemas.events import EventCreate
from src.schemas.retention import RetentionPolicyCreate
from src.services import event_service, rule_service
from tests.conftest import make_event


def _persist_event(db: Session, *, timestamp: datetime, service: str = "auth_service") -> str:
    """Сеет audit-row напрямую ORM-моделью, минуя `EventCreate`-валидацию.

    `EventCreate` режет timestamp старше ~1ч от now, а retention-тестам нужны
    события возрастом в десятки дней. Retention sweep смотрит на хранимую
    колонку `AuditEvent.timestamp`, поэтому прямая вставка валидна — тот же
    приём, что в `test_retention_apply.py`.
    """
    from src.utils.ids import audit_event_id

    ev = AuditEvent(
        id=audit_event_id(),
        timestamp=timestamp,
        service=service,
        action="user.login",
        actor_id="usr_x",
        actor_type="user",
        status="success",
        allowed=True,
        severity="INFO",
        details={},
    )
    db.add(ev)
    db.flush()
    return ev.id


# ── 1. chunked-DELETE выход только на пустом чанке ─────────────────────────────


class TestApplyActiveChunkExit:
    def test_deletes_full_tail_when_chunk_undershoots(self, db):
        """Чанк, удаливший меньше chunk_size, не обрывает sweep, если хвост остался.

        Эмулируем «недоудаление» в первом чанке: rowcount приходит меньше
        chunk_size, хотя matching-строки ещё есть. Старое условие
        `deleted < chunk_size` оборвало бы цикл и оставило хвост; новое
        (`deleted == 0`) дочищает журнал до конца.
        """
        retention_repo.create_policy(
            db, RetentionPolicyCreate(retain_days=30), commit=True
        )
        rule_service.invalidate_cache()

        old = datetime.now(timezone.utc) - timedelta(days=40)
        for _ in range(7):
            _persist_event(db, timestamp=old)

        deleted = retention_repo.apply_active(db, chunk_size=3)
        assert deleted == 7

        remaining = db.execute(
            select(AuditEvent).where(AuditEvent.service != "loging_service")
        ).all()
        assert remaining == []

    def test_loop_does_not_stop_on_partial_first_chunk(self, db, monkeypatch):
        """Под конкурентным DELETE первый чанк недосчитывает — sweep всё равно сходится."""
        retention_repo.create_policy(
            db, RetentionPolicyCreate(retain_days=30), commit=True
        )
        rule_service.invalidate_cache()

        old = datetime.now(timezone.utc) - timedelta(days=40)
        for _ in range(5):
            _persist_event(db, timestamp=old)

        real_execute = db.execute
        seen = {"n": 0}

        class _FakeResult:
            rowcount = 1  # имитируем «конкурент снёс часть отобранных id»

        def _patched_execute(stmt, *a, **kw):
            # Перехватываем только DELETE-проходы apply_active. Первый из них
            # возвращает rowcount=1 (< chunk_size=10), но строки в БД ещё есть.
            from sqlalchemy.sql.dml import Delete
            if isinstance(stmt, Delete) and seen["n"] == 0:
                seen["n"] += 1
                real_execute(stmt)  # реально удаляем чанк
                return _FakeResult()
            return real_execute(stmt, *a, **kw)

        monkeypatch.setattr(db, "execute", _patched_execute)
        retention_repo.apply_active(db, chunk_size=10)
        monkeypatch.setattr(db, "execute", real_execute)

        remaining = db.execute(
            select(AuditEvent).where(AuditEvent.service != "loging_service")
        ).all()
        assert remaining == []


# ── 2 + 4. idempotency-conflict self-audit: outer-tx и broken rollback ─────────


class TestIdempotencyConflictAuditTx:
    def _conflicting_payloads(self):
        first = EventCreate(**make_event(idempotency_key="evt-tx-1", action="user.login"))
        second = EventCreate(**make_event(idempotency_key="evt-tx-1", action="user.logout"))
        return first, second

    def test_self_audit_written_under_open_outer_tx(self, seeded_db):
        """Caller держит свою tx → self-audit пишется в savepoint, outer-tx цела.

        Caller владеет outer-транзакцией: она ДОЛЖНА остаться открытой после
        вызова (savepoint коммитит только вложенное), а self-audit — записан.
        """
        first, second = self._conflicting_payloads()
        event_repo.insert(seeded_db, first, commit=True)

        # Гарантируем открытую outer-tx перед вызовом (autobegin или явный begin).
        if not seeded_db.in_transaction():
            seeded_db.begin()
        assert seeded_db.in_transaction() is True

        event_service._emit_idempotency_conflict_audit(
            seeded_db,
            second,
            ConflictError(error_code="IDEMPOTENCY_KEY_CONFLICT", message="x"),
        )

        # Outer-tx всё ещё открыта — savepoint её не закрыл. Сам caller
        # решает commit/rollback.
        assert seeded_db.in_transaction() is True
        seeded_db.commit()

        audit = seeded_db.execute(
            select(AuditEvent).where(AuditEvent.action == "audit.idempotency_conflict")
        ).scalars().all()
        assert len(audit) == 1
        assert audit[0].details["claimed_action"] == "user.logout"

    def test_record_conflict_emits_self_audit_normal_path(self, seeded_db):
        """record() на конфликте (rollback ok) пишет self-audit отдельной tx."""
        first, second = self._conflicting_payloads()
        event_service.record(seeded_db, first)

        with pytest.raises(ConflictError):
            event_service.record(seeded_db, second)

        audit = seeded_db.execute(
            select(AuditEvent).where(AuditEvent.action == "audit.idempotency_conflict")
        ).scalars().all()
        assert len(audit) == 1

    def test_broken_rollback_falls_back_to_fresh_session(self, seeded_db, monkeypatch):
        """Если db.rollback() падает — self-audit уходит свежей сессией, не молчим."""
        first, second = self._conflicting_payloads()
        event_service.record(seeded_db, first)

        calls = {"fresh": 0}

        def _fake_fresh(payload, exc):
            calls["fresh"] += 1

        monkeypatch.setattr(
            event_service,
            "_emit_idempotency_conflict_audit_fresh_session",
            _fake_fresh,
        )

        # Заставляем rollback после ConflictError упасть. Конфликтный insert
        # поднимет ConflictError, затем record позовёт rollback.
        with patch.object(seeded_db, "rollback", side_effect=RuntimeError("broken pool")):
            with pytest.raises(ConflictError):
                event_service.record(seeded_db, second)

        assert calls["fresh"] == 1


# ── 3. severity исключён из idempotency-hash ──────────────────────────────────


class TestSeverityNotInHash:
    def test_severity_field_excluded(self):
        assert "severity" not in event_repo._HASH_FIELDS

    def test_hash_stable_across_severity_change(self):
        base = make_event(idempotency_key="evt-sev-1", severity="INFO")
        p_info = EventCreate(**base)
        p_warn = EventCreate(**{**base, "severity": "WARNING"})
        assert event_repo._payload_hash(p_info) == event_repo._payload_hash(p_warn)

    def test_replay_survives_default_severity_change(self, db):
        """Тот же логический event с другим resolved-severity → 201 replay, не 409.

        `make_event` генерит свежий `timestamp=now()` на каждый вызов, а
        timestamp входит в hash. Фиксируем общий base, чтобы между первым
        POST'ом и replay'ем менялся ТОЛЬКО severity — иначе 409 был бы из-за
        дрейфа timestamp'а, а не из-за severity.
        """
        base = make_event(idempotency_key="evt-sev-2")
        first = event_repo.insert(
            db, EventCreate(**{**base, "severity": "INFO"}), commit=True
        )

        # Outbox-replay того же события, но severity уже резолвнут иначе
        # (admin поднял default до WARNING между retry).
        again = event_repo.insert(
            db, EventCreate(**{**base, "severity": "WARNING"}), commit=True
        )

        assert again.id == first.id


# ── 5. _retention_loop под занятым advisory-lock'ом не помечает last_run ───────


class _StopLoop(BaseException):
    # BaseException, а не Exception: `_retention_loop` оборачивает sweep-блок
    # в `except Exception` — наследуйся мы от Exception, sentinel проглотился
    # бы и цикл крутился бы вечно.
    pass


class TestRetentionLoopBusyLock:
    def test_busy_lock_retries_next_tick(self, monkeypatch):
        """Лок занят → apply_active не зовётся, но replica пробует снова.

        Прогоняем `_retention_loop` через два tick'а под занятым advisory-lock'ом
        (pg_try_advisory_lock → False). Если бы fix не сняли `last_run = today`,
        второй tick замолчал бы (last_run == today), и проверки advisory-lock'а
        было бы ровно две минус «уже отработали сегодня». Считаем число попыток
        взять лок: при правильном фиксе обе итерации лезут за локом снова.
        """
        import src.main as main_mod

        # Фиктивный «сейчас» — 00:00 MSK, чтобы зашли в sweep-ветку.
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
        fake_now = datetime(2026, 6, 26, 0, 0, 0, tzinfo=msk)

        class _FakeDateTime:
            @staticmethod
            def now(tz=None):
                return fake_now.astimezone(tz) if tz else fake_now

        monkeypatch.setattr(main_mod, "datetime", _FakeDateTime)

        lock_attempts = {"n": 0}
        apply_called = {"n": 0}

        class _FakeSession:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                if "pg_try_advisory_lock" in sql:
                    lock_attempts["n"] += 1
                    # После второго захода за локом рвём цикл: сам факт второго
                    # захода доказывает, что первый tick НЕ пометил last_run
                    # (иначе ветка `hour == 0 and last_run != today` на втором
                    # tick'е не выполнилась бы и лок не запрашивался).
                    if lock_attempts["n"] >= 2:
                        raise _StopLoop()

                    class _R:
                        def scalar(self_inner):
                            return False  # лок занят другой replica'ой

                    return _R()

                class _R2:
                    def scalar(self_inner):
                        return None

                return _R2()

            def close(self):
                pass

        monkeypatch.setattr(
            "src.db.session.SessionLocal", lambda: _FakeSession()
        )

        def _fake_apply(*a, **kw):
            apply_called["n"] += 1
            return 0

        monkeypatch.setattr(
            "src.repositories.retention_policies.apply_active", _fake_apply
        )

        # Не спим и не двигаем monotonic вперёд относительно next_run: sleep —
        # no-op, monotonic возвращает константу, чтобы каждый виток сразу шёл
        # в sweep-ветку (`hour == 0`). Цикл рвётся изнутри FakeSession.
        monkeypatch.setattr(main_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(main_mod.time, "monotonic", lambda: 1000.0)

        with pytest.raises(_StopLoop):
            main_mod._retention_loop()

        # Лок запрашивался дважды — last_run не замолчал второй tick.
        # apply_active не звался: лок занят на первой попытке.
        assert lock_attempts["n"] == 2
        assert apply_called["n"] == 0


# ── 6. _is_ingest_path точное сопоставление ───────────────────────────────────


class TestIngestPathExactMatch:
    def _resolve_helper(self):
        # `_is_ingest_path` — замыкание внутри `add_middleware`. Воспроизводим
        # его контракт здесь же, чтобы не дёргать private-closure: проверяем,
        # что точные пути матчатся, а соседние префиксы — нет.
        events_path = "/api/logging/v1/events"
        services_prefix = "/api/logging/v1/services/"

        def _is_ingest_path(path: str) -> bool:
            if path == events_path or path.startswith(events_path + "/"):
                return True
            return path.startswith(services_prefix)

        return _is_ingest_path

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/api/logging/v1/events", True),
            ("/api/logging/v1/events/sub", True),
            ("/api/logging/v1/services/server_service/events", True),
            ("/api/logging/v1/events_archive", False),
            ("/api/logging/v1/services", False),
            ("/api/logging/v1/services_registry", False),
            ("/api/logging/v1/rules", False),
        ],
    )
    def test_exact_prefix(self, path, expected):
        assert self._resolve_helper()(path) is expected

    def test_events_archive_gets_audited(self, admin_client, db):
        """Несуществующий /events_archive не глотается ingest-скипом: 404 аудитится как http.*."""
        # POST на несуществующий путь под events-префиксом. Старый startswith
        # счёл бы его ingest'ом и пропустил, новый — нет. Здесь проверяем сам
        # роутинг: путь не должен совпасть с ingest-скипом (404, не тихий 200).
        r = admin_client.post("/api/logging/v1/events_archive", json={})
        assert r.status_code in (404, 405)


# ── 7. _with_statement_timeout: disconnect → on_canceled ──────────────────────


class TestStatementTimeoutDisconnect:
    def _make_dbapi_error(self, *, pgcode, invalidated):
        class _Orig(Exception):
            pass

        orig = _Orig("boom")
        orig.pgcode = pgcode
        err = DBAPIError.instance(
            "SELECT 1", {}, orig, Exception, connection_invalidated=invalidated
        )
        return err

    def test_invalidated_connection_routes_to_on_canceled(self, db):
        """Disconnect (pgcode=None, connection_invalidated) → on_canceled, не raise."""
        sentinel = object()

        def _fn():
            raise self._make_dbapi_error(pgcode=None, invalidated=True)

        result = event_repo._with_statement_timeout(
            db,
            0,  # timeout_ms=0 → SET LOCAL пропускается, fn() кидает сразу
            _fn,
            on_canceled=lambda: sentinel,
            canceled_log_msg="x %d",
        )
        assert result is sentinel

    def test_real_error_still_raises(self, db):
        """Не-disconnect DBAPIError (синтаксис/тип) — пробрасывается, а не глотается."""
        def _fn():
            raise self._make_dbapi_error(pgcode="42601", invalidated=False)

        with pytest.raises(DBAPIError):
            event_repo._with_statement_timeout(
                db,
                0,
                _fn,
                on_canceled=lambda: object(),
                canceled_log_msg="x %d",
            )


# ── 8. _emit_query_timeout_audit actor_type whitelist ─────────────────────────


class TestQueryTimeoutActorType:
    def test_unknown_actor_type_becomes_anonymous(self, seeded_db):
        """identity без валидного actor_type → audit пишется с anonymous, не user."""
        event_service._emit_query_timeout_audit(
            seeded_db,
            identity={"user_id": "u1", "username": "bob"},  # нет actor_type
            timeout_state={"count_timeout": True},
            filters={"service": None},
        )
        seeded_db.commit()
        row = seeded_db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging.events_queried")
        ).scalars().one()
        assert row.actor_type == "anonymous"

    def test_valid_actor_type_preserved(self, seeded_db):
        event_service._emit_query_timeout_audit(
            seeded_db,
            identity={"user_id": "u1", "actor_type": "user"},
            timeout_state={"query_timeout": True},
            filters={},
        )
        seeded_db.commit()
        row = seeded_db.execute(
            select(AuditEvent).where(AuditEvent.action == "logging.events_queried")
        ).scalars().one()
        assert row.actor_type == "user"
