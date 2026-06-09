"""`_drain_running_tasks` пишет `worker_id` в payload `task.worker_shutdown`.

P1-fix F4: без worker_id audit-row нельзя связать с pod'ом, в котором случился
drain (terminationGracePeriodSeconds, OOM, rolling-update). Берём `worker_id`
из `tasks.worker_id` (тот же, что писал mark_running); если row была в QUEUED
pre-commit-окне и `worker_id` ещё None — fallback на `_runner_state.get_worker_id()`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from taskiq import TaskiqState

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.repositories import task as task_repo


def _new_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:16]}"


class TestDrainAuditWorkerId:
    async def test_running_task_audit_includes_worker_id_from_row(
        self, monkeypatch,
    ):
        """RUNNING task с worker_id='worker_A' → audit пишет тот же worker_id."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_w2c",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
                "worker_id": "worker_A_drain",
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(main_settings, "worker_shutdown_timeout_seconds", 0.2)

        try:
            await _drain_running_tasks(TaskiqState())
        finally:
            RUNNING_TASKS.discard(tid)

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(AuditOutbox).where(AuditOutbox.task_id == tid)
            )).scalars().all()
            shutdown_rows = [
                r for r in rows
                if (r.payload or {}).get("action") == "task.worker_shutdown"
            ]
            assert len(shutdown_rows) == 1
            details = shutdown_rows[0].payload["details"]
            assert details.get("worker_id") == "worker_A_drain", (
                f"audit details.worker_id ожидался 'worker_A_drain', got: {details!r}"
            )

    async def test_queued_pre_commit_task_audit_falls_back_to_process_worker_id(
        self, monkeypatch,
    ):
        """Pre-commit окно: row в QUEUED, worker_id ещё None. Audit пишет
        process-local идентификатор реплики (`get_worker_id()`)."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS, get_worker_id

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_w2c",
                "payload": {},
                # QUEUED + worker_id=NULL имитирует pre-commit window
                # (register_running_task сделан, но mark_running CAS ещё
                # не отработал).
                "status": TaskStatus.QUEUED,
                "attempt": 0,
                "max_attempts": 3,
                "worker_id": None,
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(main_settings, "worker_shutdown_timeout_seconds", 0.2)

        expected_wid = get_worker_id()

        try:
            await _drain_running_tasks(TaskiqState())
        finally:
            RUNNING_TASKS.discard(tid)

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(AuditOutbox).where(AuditOutbox.task_id == tid)
            )).scalars().all()
            shutdown_rows = [
                r for r in rows
                if (r.payload or {}).get("action") == "task.worker_shutdown"
            ]
            assert len(shutdown_rows) == 1
            details = shutdown_rows[0].payload["details"]
            assert details.get("worker_id") == expected_wid, (
                f"audit details.worker_id для QUEUED pre-commit-row должен "
                f"быть процессным {expected_wid!r}, got: {details!r}"
            )
