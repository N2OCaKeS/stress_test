"""Тесты quality-фиксов для server_worker.

Покрывает:

* **LAST_ERROR_MAX_LEN constant** — вынесен в `src/core/constants.py` и
  применяется в обоих publisher-truncation'ах.
* **cleanup_stale_heartbeats** — periodic `worker.cleanup_stale_heartbeats`
  удаляет row'ы старше cleanup-порога, оставляет свежие.
* **list_due_scheduled_retries** — SELECT с `FOR UPDATE SKIP LOCKED`:
  при concurrent recovery две replica'и партиционируют due-rows,
  а не дублируют их в Redis.

Все тесты — против реальной PostgreSQL, без моков БД.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.core.constants import LAST_ERROR_MAX_LEN, TaskStatus
from src.db.session import AsyncSessionLocal, engine
from src.models import AuditOutbox, WorkerHeartbeat
from src.repositories import task as task_repo


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ LAST_ERROR_MAX_LEN constant                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestLastErrorMaxLenConstant:
    """Constant вынесен в `core/constants.py`, оба publisher-truncation'а
    используют его (не magic-512)."""

    def test_constant_is_512(self):
        assert LAST_ERROR_MAX_LEN == 512

    def test_publisher_module_imports_constant(self):
        """`audit_outbox_publisher` должен импортировать константу — это
        ловит регресс возврата к magic-512."""
        import src.services.audit_outbox_publisher as pub

        # Импортированное имя должно быть тем же объектом.
        assert pub.LAST_ERROR_MAX_LEN is LAST_ERROR_MAX_LEN

    async def test_audit_emit_error_truncates_to_constant(self, monkeypatch):
        """Симулируем `AuditEmitError` с очень длинным message → `last_error`
        обрезается до `LAST_ERROR_MAX_LEN`."""
        from src.services import audit_client, audit_outbox_publisher

        # Очень длинная error_message — заведомо больше 512.
        long_message = "x" * 2000

        async def fake_emit(action, **kwargs):
            raise audit_client.AuditEmitError(long_message)

        monkeypatch.setattr(audit_client, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit", fake_emit
        )

        # Вставляем outbox-row и зовём single-pass publisher.
        async with AsyncSessionLocal() as session:
            session.add(
                AuditOutbox(
                    task_id=_new_id(),
                    payload={
                        "action": "server.power_on",
                        "status": "success",
                        "allowed": True,
                        "target_id": "srv_test",
                    },
                )
            )
            await session.commit()

        await audit_outbox_publisher.flush_outbox()

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(select(AuditOutbox))
            ).scalar_one()

        assert row.published_at is None  # AuditEmitError → unpublished
        assert row.last_error is not None
        assert len(row.last_error) <= LAST_ERROR_MAX_LEN

    async def test_generic_exception_truncates_to_constant(self, monkeypatch):
        """Не-AuditEmitError exception в `_publish_one` тоже truncate'ит."""
        from src.services import audit_outbox_publisher

        async def fake_emit(action, **kwargs):
            # Длинный текст — `type(exc).__name__: exc` явно больше 512.
            raise RuntimeError("y" * 2000)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit", fake_emit
        )

        async with AsyncSessionLocal() as session:
            session.add(
                AuditOutbox(
                    task_id=_new_id(),
                    payload={
                        "action": "server.power_on",
                        "status": "success",
                        "allowed": True,
                        "target_id": "srv_test",
                    },
                )
            )
            await session.commit()

        await audit_outbox_publisher.flush_outbox()

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(select(AuditOutbox))
            ).scalar_one()

        assert row.published_at is None
        assert row.last_error is not None
        assert len(row.last_error) <= LAST_ERROR_MAX_LEN


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ worker.cleanup_stale_heartbeats                                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestCleanupStaleHeartbeats:
    """Periodic `worker.cleanup_stale_heartbeats` дропает row'ы старше
    cutoff'а, оставляет свежие."""

    async def test_cleanup_drops_old_keeps_fresh(self, monkeypatch):
        """Старый row (≥7d) удалён, свежий — нет."""
        from src.main import _settings, worker_cleanup_stale_heartbeats

        # Тестовый порог — 7 дней (дефолт).
        threshold = _settings.worker_heartbeat_cleanup_threshold_seconds
        now = datetime.now(timezone.utc)

        old_at = now - timedelta(seconds=threshold + 86400)  # на сутки старше
        fresh_at = now - timedelta(minutes=5)

        async with AsyncSessionLocal() as session:
            session.add(
                WorkerHeartbeat(worker_id="dead-pod-A", last_heartbeat_at=old_at)
            )
            session.add(
                WorkerHeartbeat(
                    worker_id="alive-pod-B", last_heartbeat_at=fresh_at
                )
            )
            await session.commit()

        if hasattr(worker_cleanup_stale_heartbeats, "original_func"):
            await worker_cleanup_stale_heartbeats.original_func()
        else:
            await worker_cleanup_stale_heartbeats()

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(WorkerHeartbeat).order_by(
                        WorkerHeartbeat.worker_id
                    )
                )
            ).scalars().all()

        worker_ids = [r.worker_id for r in rows]
        assert worker_ids == ["alive-pod-B"]

    async def test_cleanup_noop_when_all_fresh(self):
        """Все row'ы свежие → ничего не удаляется, exception не бросается."""
        from src.main import worker_cleanup_stale_heartbeats

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            for wid in ("pod-1", "pod-2", "pod-3"):
                session.add(
                    WorkerHeartbeat(
                        worker_id=wid,
                        last_heartbeat_at=now - timedelta(minutes=1),
                    )
                )
            await session.commit()

        if hasattr(worker_cleanup_stale_heartbeats, "original_func"):
            await worker_cleanup_stale_heartbeats.original_func()
        else:
            await worker_cleanup_stale_heartbeats()

        async with AsyncSessionLocal() as session:
            count = len(
                (
                    await session.execute(select(WorkerHeartbeat))
                ).scalars().all()
            )
        assert count == 3

    async def test_cleanup_with_custom_threshold(self, monkeypatch):
        """Если порог 60s, row 2-минутной давности тоже удаляется."""
        from src.main import _settings, worker_cleanup_stale_heartbeats

        monkeypatch.setattr(
            _settings, "worker_heartbeat_cleanup_threshold_seconds", 60.0
        )

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            session.add(
                WorkerHeartbeat(
                    worker_id="should-be-dropped",
                    last_heartbeat_at=now - timedelta(minutes=2),
                )
            )
            session.add(
                WorkerHeartbeat(
                    worker_id="should-stay",
                    last_heartbeat_at=now - timedelta(seconds=10),
                )
            )
            await session.commit()

        if hasattr(worker_cleanup_stale_heartbeats, "original_func"):
            await worker_cleanup_stale_heartbeats.original_func()
        else:
            await worker_cleanup_stale_heartbeats()

        async with AsyncSessionLocal() as session:
            ids = [
                r.worker_id
                for r in (
                    await session.execute(
                        select(WorkerHeartbeat).order_by(
                            WorkerHeartbeat.worker_id
                        )
                    )
                ).scalars().all()
            ]
        assert ids == ["should-stay"]

    async def test_cleanup_task_registered_on_broker(self):
        """Task должна быть зарегистрирована — иначе scheduler её не
        kiq'нет и cleanup никогда не сработает."""
        from src.main import broker

        assert "worker.cleanup_stale_heartbeats" in broker.get_all_tasks()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ list_due_scheduled_retries: FOR UPDATE SKIP LOCKED                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestDueScheduledRetriesSkipLocked:
    """SELECT due-row'ов идёт с `FOR UPDATE SKIP LOCKED`: при concurrent
    startup двух replica'ах одна и та же row не выбирается дважды."""

    async def test_repo_emits_for_update_skip_locked(self):
        """Repository action под капотом отправляет в PG `FOR UPDATE SKIP LOCKED`.

        Перехватываем SQL через `do_execute`-hook движка и проверяем, что
        строка содержит обе клаузы. Это regression-guard на случай, если
        кто-то откатит `with_for_update(skip_locked=True)` в репозитории.
        """
        from sqlalchemy import event

        captured: list[str] = []

        def before_execute(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        # Хук на sync_engine (asyncpg/psycopg идут через него).
        event.listen(engine.sync_engine, "before_cursor_execute", before_execute)
        try:
            async with AsyncSessionLocal() as session:
                await task_repo.list_due_scheduled_retries(session)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_execute)

        # Ищем SELECT, содержащий FOR UPDATE SKIP LOCKED. SQLAlchemy
        # эмитит несколько утилитарных запросов (BEGIN, current_timestamp);
        # фильтруем по «FROM tasks».
        select_statements = [s for s in captured if "FROM tasks" in s]
        assert select_statements, f"no SELECT against tasks captured: {captured}"
        sql_upper = select_statements[-1].upper()
        assert "FOR UPDATE" in sql_upper
        assert "SKIP LOCKED" in sql_upper

    async def test_concurrent_recovery_partitions_due_rows(self):
        """E2E на реальной PG: две параллельные сессии SELECT'ят
        due-row'ы. С SKIP LOCKED они получают непересекающиеся подмножества.

        Сценарий: вставляем 4 due-row'ы. Replica A SELECT'ит и держит
        транзакцию (lock на rows). Replica B параллельно делает SELECT
        — без SKIP LOCKED он бы заблокировался на lock или вернул те же
        rows; с SKIP LOCKED он мгновенно получает 0 rows (все заблокированы)
        — это и есть «партиционирование».
        """
        now = datetime.now(timezone.utc)
        due_ids = [_new_id() for _ in range(4)]
        async with AsyncSessionLocal() as session:
            for tid in due_ids:
                await task_repo.create(session, {
                    "id": tid,
                    "task_kind": "power.on",
                    "target_server_id": "srv_test",
                    "payload": {},
                    "status": TaskStatus.QUEUED,
                    "attempt": 1,
                    "max_attempts": 3,
                    "scheduled_retry_at": now - timedelta(seconds=10),
                })
            await session.commit()

        # ── Replica A: SELECT и держит lock'и до rollback'а ──────────────
        # ── Replica B параллельно: SKIP LOCKED → видит пустой результат ─
        session_a = AsyncSessionLocal()
        session_b = AsyncSessionLocal()
        await session_a.__aenter__()
        await session_b.__aenter__()
        try:
            rows_a = await task_repo.list_due_scheduled_retries(session_a)
            assert {r.id for r in rows_a} == set(due_ids)

            # Пока A держит транзакцию, B видит 0 rows — все заблокированы.
            # SKIP LOCKED — non-blocking: если бы lock-механизм требовал
            # ждать, asyncio.wait_for выбросил бы TimeoutError.
            rows_b = await asyncio.wait_for(
                task_repo.list_due_scheduled_retries(session_b),
                timeout=3.0,
            )
            assert rows_b == [], (
                f"SKIP LOCKED не сработал: B видит {[r.id for r in rows_b]}"
            )
        finally:
            await session_b.__aexit__(None, None, None)
            await session_a.__aexit__(None, None, None)

    async def test_recovery_uses_skip_locked_path(self, monkeypatch):
        """Smoke: `_recover_scheduled_retries` корректно отрабатывает на
        due-row'ах при skip-locked SELECT'е. (Регрессия-guard: фикс не
        сломал happy-path.)"""
        from src.main import _recover_scheduled_retries, broker
        from taskiq import TaskiqState

        now = datetime.now(timezone.utc)
        due_ids = [_new_id() for _ in range(3)]
        async with AsyncSessionLocal() as session:
            for tid in due_ids:
                await task_repo.create(session, {
                    "id": tid,
                    "task_kind": "power.on",
                    "target_server_id": "srv_test",
                    "payload": {},
                    "status": TaskStatus.QUEUED,
                    "attempt": 1,
                    "max_attempts": 3,
                    "scheduled_retry_at": now - timedelta(seconds=5),
                })
            await session.commit()

        kicked: list[str] = []

        class FakeKicker:
            async def kiq(self, task_id, *args, **kwargs):
                kicked.append(task_id)

        class FakeTask:
            def kicker(self):
                return FakeKicker()

        monkeypatch.setattr(broker, "find_task", lambda kind: FakeTask())

        await _recover_scheduled_retries(TaskiqState())

        assert sorted(kicked) == sorted(due_ids)
