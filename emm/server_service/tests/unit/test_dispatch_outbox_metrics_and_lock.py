"""dispatch_outbox: `pending_count` + `list_pending` под FOR UPDATE SKIP LOCKED.

Закрывает два cov-gap'а:

* `pending_count` — метрика ops, никогда не вызывалась в тестах. Если
  фильтр в репозитории сломается (опечатка в column ref, обратный
  invariant), `dispatch_outbox_pending_depth` начнёт врать молча.
* `list_pending(with_for_update_skip_locked=True)` — production-дефолт,
  на одной session-fixture деградирует в обычный SELECT. Чтобы проверить
  семантику SKIP LOCKED, нужны две независимые БД-сессии (как в
  `test_server_account_lock.py`).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models import DispatchOutbox
from src.repositories import dispatch_outbox as dispatch_outbox_repo


# Отдельный engine — SKIP LOCKED работает только между независимыми
# connection'ами; в SAVEPOINT-fixture'е (`db`) всё ездит на одном connection'е,
# где FOR UPDATE на одной строке деградирует до noop'а.
_engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


# ── pending_count ───────────────────────────────────────────────────────────


class TestPendingCount:
    """`pending_count` считает `dispatched_at IS NULL` и игнорирует остальное."""

    async def test_counts_only_pending_rows(self, db):
        """N pending + M dispatched → result == N."""
        # Срезаем потенциальные хвосты от соседних тестов в одной сессии-fixture
        # (других быть не должно, но dispatch_outbox используется в нескольких
        # тестах рядом — на всякий случай начинаем со снепшота).
        baseline = await dispatch_outbox_repo.pending_count(db)

        # 3 pending
        for i in range(3):
            await dispatch_outbox_repo.insert(
                db,
                task_id=f"tsk_pc_p_{i}",
                task_kind="power.on",
                payload={"i": i},
            )
        # 2 dispatched
        dispatched_ids = []
        for i in range(2):
            row = await dispatch_outbox_repo.insert(
                db,
                task_id=f"tsk_pc_d_{i}",
                task_kind="power.off",
                payload={"i": i},
            )
            await db.flush()
            dispatched_ids.append(row.id)
        for rid in dispatched_ids:
            await dispatch_outbox_repo.mark_dispatched(db, rid)
        await db.flush()

        result = await dispatch_outbox_repo.pending_count(db)
        assert result == baseline + 3

    async def test_zero_on_empty(self, db):
        """Снимок depth'а на пустой таблице — ноль, не NULL/None."""
        # Чистим — fixture-rollback это откатит, но в рамках теста
        # начинаем с гарантированно пустой таблицы.
        await db.execute(delete(DispatchOutbox))
        await db.flush()

        assert await dispatch_outbox_repo.pending_count(db) == 0

    async def test_next_retry_at_does_not_affect_count(self, db):
        """`pending_count` смотрит только на `dispatched_at IS NULL`.

        Row с `next_retry_at` в будущем (backoff) формально pending —
        depth-метрика обязана его учитывать, иначе оператор увидит ноль и
        не заметит, что вся очередь зависла на retry-backoff'е.
        """
        await db.execute(delete(DispatchOutbox))
        await db.flush()

        # Один pending, ещё не пробовали
        await dispatch_outbox_repo.insert(
            db, task_id="tsk_pc_now", task_kind="power.on", payload={}
        )
        # Второй pending, но в backoff'е (next_retry_at в будущем)
        row_b = await dispatch_outbox_repo.insert(
            db, task_id="tsk_pc_backoff", task_kind="power.on", payload={}
        )
        await db.flush()
        await dispatch_outbox_repo.mark_failed(
            db,
            row_b.id,
            error="broker hiccup",
            next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        await db.flush()

        # Оба попадают в depth — это важно для ops-сигнала
        assert await dispatch_outbox_repo.pending_count(db) == 2


# ── list_pending(with_for_update_skip_locked=True) ──────────────────────────


@pytest_asyncio.fixture
async def isolated_pending_row():
    """Создать одну pending outbox-row в собственной транзакции.

    Чистим за собой — мы вне SAVEPOINT-fixture'ы, обычный rollback теста
    эту строку не уберёт.
    """
    task_id = f"tsk_skiplock_{uuid.uuid4().hex[:8]}"
    row_id: uuid.UUID
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            row = DispatchOutbox(
                task_id=task_id,
                task_kind="power.on",
                payload={"server_id": "srv_x"},
            )
            session.add(row)
            await session.flush()
            row_id = row.id

    try:
        yield {"task_id": task_id, "row_id": row_id}
    finally:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    delete(DispatchOutbox).where(DispatchOutbox.id == row_id)
                )


class TestListPendingSkipLocked:
    """`list_pending(with_for_update_skip_locked=True)` — concurrent poller'ы."""

    async def test_second_session_skips_locked_row(self, isolated_pending_row):
        """Второй poller не видит row, которую держит первый.

        Это и есть смысл SKIP LOCKED: две реплики дозатора берут разные
        порции pending'а, никогда не дерутся за одну и ту же row.
        """
        task_id = isolated_pending_row["task_id"]

        first_locked = asyncio.Event()
        release_first = asyncio.Event()
        second_result: list[list[DispatchOutbox]] = []

        async def first_holder():
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    rows = await dispatch_outbox_repo.list_pending(
                        session, limit=10, with_for_update_skip_locked=True
                    )
                    # Наша строка должна попасть в выборку первого poller'а.
                    assert any(r.task_id == task_id for r in rows), (
                        "первый poller обязан был забрать pending row"
                    )
                    first_locked.set()
                    await release_first.wait()

        async def second_poller():
            await first_locked.wait()
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    rows = await dispatch_outbox_repo.list_pending(
                        session, limit=10, with_for_update_skip_locked=True
                    )
                    second_result.append(rows)
            release_first.set()

        await asyncio.gather(first_holder(), second_poller())

        assert len(second_result) == 1
        ids = {r.task_id for r in second_result[0]}
        assert task_id not in ids, (
            "второй poller с SKIP LOCKED не должен видеть row, "
            "захваченную первой транзакцией"
        )

    async def test_skip_locked_returns_other_pending_rows(
        self, isolated_pending_row
    ):
        """SKIP LOCKED отдаёт остальные pending row'ы, не залоченные первой
        транзакцией. Воспроизводит реалистичный сценарий двух poller-реплик:
        каждая забирает свою порцию очереди.
        """
        held_task_id = isolated_pending_row["task_id"]

        # Положим ещё одну pending row в отдельной транзакции — её первый
        # poller тоже захватит, но потом мы добавим третью «незахваченную»
        # после того, как первый уже зафиксировал свою выборку.
        extra_task_id = f"tsk_skiplock_extra_{uuid.uuid4().hex[:8]}"
        extra_row_id: uuid.UUID
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                extra = DispatchOutbox(
                    task_id=extra_task_id,
                    task_kind="power.on",
                    payload={"server_id": "srv_y"},
                )
                session.add(extra)
                await session.flush()
                extra_row_id = extra.id

        try:
            first_locked = asyncio.Event()
            release_first = asyncio.Event()
            second_rows: list[list[DispatchOutbox]] = []

            async def first_holder():
                async with AsyncSession(_engine, expire_on_commit=False) as session:
                    async with session.begin():
                        # limit=1 — первый забирает ровно одну row из FIFO.
                        rows = await dispatch_outbox_repo.list_pending(
                            session, limit=1, with_for_update_skip_locked=True
                        )
                        assert len(rows) == 1
                        first_locked.set()
                        await release_first.wait()

            async def second_poller():
                await first_locked.wait()
                async with AsyncSession(_engine, expire_on_commit=False) as session:
                    async with session.begin():
                        rows = await dispatch_outbox_repo.list_pending(
                            session, limit=10, with_for_update_skip_locked=True
                        )
                        second_rows.append(rows)
                release_first.set()

            await asyncio.gather(first_holder(), second_poller())

            assert len(second_rows) == 1
            ids = {r.task_id for r in second_rows[0]}
            # Хотя бы одна из двух наших pending row'ок должна была остаться
            # незалоченной для второго poller'а (первый взял только limit=1).
            assert (held_task_id in ids) or (extra_task_id in ids), (
                "второй poller с SKIP LOCKED обязан увидеть pending row, "
                "не захваченную первой транзакцией"
            )
        finally:
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    await session.execute(
                        delete(DispatchOutbox).where(
                            DispatchOutbox.id == extra_row_id
                        )
                    )
