"""Приоритет в claim'е due-retry-row'ов.

`claim_one_due_scheduled_retry` теперь сортирует
`priority DESC, scheduled_retry_at ASC, id ASC`:

* high-priority row подбирается раньше старого normal'а;
* при равном priority — FIFO по `scheduled_retry_at` (самый просроченный);
* обычный `task_repo.create` без явного priority кладёт нормальный (0).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


async def _seed(*, priority: int, retry_offset_s: int) -> str:
    """Одна due-retry-row с заданным priority и временем retry'я (now - offset)."""
    now = datetime.now(timezone.utc)
    tid = _new_id()
    async with AsyncSessionLocal() as session:
        await task_repo.create(session, {
            "id": tid,
            "task_kind": "power.on",
            "target_server_id": "srv_prio",
            "payload": {},
            "status": TaskStatus.QUEUED,
            "attempt": 1,
            "max_attempts": 3,
            "priority": priority,
            "scheduled_retry_at": now - timedelta(seconds=retry_offset_s),
        })
        await session.commit()
    return tid


class TestClaimPriorityOrder:
    async def test_high_priority_claimed_before_older_normal(self):
        """High-priority row подбирается раньше normal'а, который встал в
        очередь раньше (большее retry_offset)."""
        normal_old = await _seed(priority=0, retry_offset_s=60)
        high_new = await _seed(priority=100, retry_offset_s=5)

        async with AsyncSessionLocal() as session:
            claimed = await task_repo.claim_one_due_scheduled_retry(session)
            await session.commit()

        assert claimed is not None
        assert claimed.id == high_new, (
            f"ожидали high-priority {high_new}, получили {claimed.id} "
            f"(normal_old={normal_old})"
        )

    async def test_equal_priority_is_fifo_by_retry_time(self):
        """При равном priority claim берёт самый просроченный (FIFO по
        scheduled_retry_at)."""
        older = await _seed(priority=0, retry_offset_s=120)
        newer = await _seed(priority=0, retry_offset_s=10)

        async with AsyncSessionLocal() as session:
            claimed = await task_repo.claim_one_due_scheduled_retry(session)
            await session.commit()

        assert claimed is not None
        assert claimed.id == older, (
            f"ожидали самый старый {older}, получили {claimed.id} (newer={newer})"
        )

    async def test_full_drain_orders_high_then_fifo(self):
        """Последовательный drain: сначала high, затем normal'ы в FIFO-порядке."""
        normal_old = await _seed(priority=0, retry_offset_s=90)
        normal_new = await _seed(priority=0, retry_offset_s=20)
        high = await _seed(priority=100, retry_offset_s=1)

        claimed_order: list[str] = []
        async with AsyncSessionLocal() as session:
            while True:
                row = await task_repo.claim_one_due_scheduled_retry(session)
                if row is None:
                    break
                claimed_order.append(row.id)
                await session.commit()

        assert claimed_order == [high, normal_old, normal_new], claimed_order


class TestDefaultPriority:
    async def test_plain_create_defaults_to_normal(self):
        """`create` без priority в data → priority == 0 (normal)."""
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "inventory.sync",
                "payload": {},
            })
            await session.commit()

        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.priority == 0
