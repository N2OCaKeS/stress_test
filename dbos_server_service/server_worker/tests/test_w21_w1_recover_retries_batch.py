"""Per-row claim для `_recover_due_scheduled_retries_once`.

Старый код: один SELECT с `FOR UPDATE SKIP LOCKED` держал lock на ВСЕ
due-row'ы пока шёл kiq-loop (N×Redis-RTT). При backlog'е 100+ это
блокировало parallel-replica и sweep-task'и.

Новый код: per-row короткая транзакция
`SELECT ... FOR UPDATE LIMIT 1 → UPDATE scheduled_retry_at=NULL → COMMIT`,
после commit'а kiq уходит без открытой транзакции.

Тесты:
* 2 параллельных «replica»-вызова `_recover_due_scheduled_retries_once`
  на disjoint-naborе due-row'ов не приводят к двойному kiq'у.
* kiq() падает на одном task'е → `scheduled_retry_at` восстанавливается,
  следующий тик его подберёт.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from tests._helpers.broker_mocks import make_broker


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


async def _seed_due(n: int) -> list[str]:
    now = datetime.now(timezone.utc)
    ids: list[str] = []
    async with AsyncSessionLocal() as session:
        for _ in range(n):
            tid = _new_id()
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_w21",
                "payload": {},
                "status": TaskStatus.QUEUED,
                "attempt": 1,
                "max_attempts": 3,
                "scheduled_retry_at": now - timedelta(seconds=5),
            })
            ids.append(tid)
        await session.commit()
    return ids


class TestParallelReplicasDisjointClaim:
    async def test_two_replicas_dont_double_kiq(self, monkeypatch):
        """Две параллельные «replica»-итерации recovery'я claim'ают непересекающиеся
        task'и: каждый task получает ровно один kiq."""
        from src.main import _recover_due_scheduled_retries_once, broker

        ids = await _seed_due(6)

        fake_broker = make_broker()
        monkeypatch.setattr(broker, "find_task", fake_broker.find_task)

        await asyncio.gather(
            _recover_due_scheduled_retries_once(),
            _recover_due_scheduled_retries_once(),
        )

        kicked = [c.args[0] for c in fake_broker._kiq.call_args_list]
        # Каждый из 6 task'ов поднят ровно один раз. Никаких дубликатов.
        assert sorted(kicked) == sorted(ids), (
            f"kicked={sorted(kicked)} ids={sorted(ids)}"
        )

    async def test_claim_clears_scheduled_retry_at(self, monkeypatch):
        """После claim'а `scheduled_retry_at` снят — следующий тик НЕ
        подберёт уже kiq'нутый row повторно (защита от echo)."""
        from src.main import _recover_due_scheduled_retries_once, broker

        ids = await _seed_due(3)

        fake_broker = make_broker()
        monkeypatch.setattr(broker, "find_task", fake_broker.find_task)

        await _recover_due_scheduled_retries_once()

        async with AsyncSessionLocal() as session:
            for tid in ids:
                row = await task_repo.get_by_id(session, tid)
                assert row is not None
                assert row.scheduled_retry_at is None, (
                    f"task {tid}: scheduled_retry_at не сброшен после claim'а"
                )
                # Статус остаётся queued — consumer на той стороне kiq'а
                # сделает mark_running CAS.
                assert row.status == TaskStatus.QUEUED


class TestBatchClaimByOneReplica:
    async def test_five_due_rows_get_five_kiqs_in_single_pass(self, monkeypatch):
        """Один вызов `_recover_due_scheduled_retries_once` поднимает все 5
        due-row'ов (per-row claim в цикле), у каждого после прохода
        `scheduled_retry_at=None`."""
        from src.main import _recover_due_scheduled_retries_once, broker

        ids = await _seed_due(5)

        fake_broker = make_broker()
        monkeypatch.setattr(broker, "find_task", fake_broker.find_task)

        await _recover_due_scheduled_retries_once()

        kicked = [c.args[0] for c in fake_broker._kiq.call_args_list]
        assert sorted(kicked) == sorted(ids), (
            f"kicked={sorted(kicked)} ids={sorted(ids)}"
        )

        async with AsyncSessionLocal() as session:
            for tid in ids:
                row = await task_repo.get_by_id(session, tid)
                assert row is not None
                assert row.scheduled_retry_at is None
                assert row.status == TaskStatus.QUEUED


class TestKiqFailureReleasesClaim:
    async def test_kiq_failure_restores_scheduled_retry_at(self, monkeypatch):
        """Если kiq() падает на конкретном task'е, `scheduled_retry_at`
        восстанавливается — row остаётся due, следующий тик подберёт."""
        from src.main import _recover_due_scheduled_retries_once, broker

        ids = await _seed_due(3)

        # kiq для одного конкретного id всегда падает.
        failing_id = ids[1]

        class FakeKicker:
            def __init__(self, fail_id: str):
                self._fail = fail_id

            async def kiq(self, task_id, *args, **kwargs):
                if task_id == self._fail:
                    raise RuntimeError("redis transient")

        class FakeTask:
            def __init__(self, fail_id: str):
                self._fail = fail_id

            def kicker(self):
                return FakeKicker(self._fail)

        monkeypatch.setattr(
            broker, "find_task", lambda kind: FakeTask(failing_id)
        )

        await _recover_due_scheduled_retries_once()

        async with AsyncSessionLocal() as session:
            for tid in ids:
                row = await task_repo.get_by_id(session, tid)
                assert row is not None
                if tid == failing_id:
                    assert row.scheduled_retry_at is not None, (
                        "kiq упал, но scheduled_retry_at не возвращён в pool"
                    )
                    assert row.status == TaskStatus.QUEUED
                else:
                    assert row.scheduled_retry_at is None, (
                        f"task {tid}: scheduled_retry_at должен быть None "
                        "после успешного claim+kiq"
                    )

    async def test_unknown_task_kind_releases_claim(self, monkeypatch):
        """Если broker не знает task_kind (миграция вырезала), claim
        должен быть отпущен, чтобы следующая раскатка не «потеряла» row."""
        from src.main import _recover_due_scheduled_retries_once, broker

        ids = await _seed_due(1)

        monkeypatch.setattr(broker, "find_task", lambda kind: None)

        await _recover_due_scheduled_retries_once()

        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, ids[0])
            assert row is not None
            assert row.status == TaskStatus.QUEUED
            assert row.scheduled_retry_at is not None, (
                "unknown task_kind — claim не отпущен, row потеряна"
            )


class TestClaimRepoFn:
    """Прямые юниты `claim_one_due_scheduled_retry`."""

    async def test_claim_returns_none_when_no_due_rows(self):
        async with AsyncSessionLocal() as session:
            row = await task_repo.claim_one_due_scheduled_retry(session)
        assert row is None

    async def test_claim_skips_future_due(self):
        """`scheduled_retry_at` в будущем — НЕ возвращается."""
        now = datetime.now(timezone.utc)
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_w21",
                "payload": {},
                "status": TaskStatus.QUEUED,
                "scheduled_retry_at": now + timedelta(hours=1),
            })
            await session.commit()

        async with AsyncSessionLocal() as session:
            row = await task_repo.claim_one_due_scheduled_retry(session)
        assert row is None

    async def test_claim_clears_retry_at_and_keeps_queued_status(self):
        ids = await _seed_due(1)
        async with AsyncSessionLocal() as session:
            row = await task_repo.claim_one_due_scheduled_retry(session)
            await session.commit()
        assert row is not None
        assert row.id == ids[0]
        assert row.scheduled_retry_at is None
        assert row.status == TaskStatus.QUEUED

    async def test_release_after_claim_restores_retry_at(self):
        ids = await _seed_due(1)
        async with AsyncSessionLocal() as session:
            row = await task_repo.claim_one_due_scheduled_retry(session)
            await session.commit()
        assert row is not None

        async with AsyncSessionLocal() as session:
            await task_repo.release_claimed_retry(session, ids[0])
            await session.commit()

        async with AsyncSessionLocal() as session:
            fresh = await task_repo.get_by_id(session, ids[0])
        assert fresh is not None
        assert fresh.scheduled_retry_at is not None
        assert fresh.status == TaskStatus.QUEUED
