"""Tests for the operator-cancel fast-path in `_runner.run_task`.

Сценарий: server_service дёрнул `POST /api/server/v1/tasks/{id}/cancel`,
который UPDATE'нул row в `tasks` со status='cancelled' ДО того, как
worker подобрал сообщение из Redis. Когда taskiq-impl вызывает
`run_task(task_id)` — `_runner` видит CANCELLED статус, пропускает
dispatch, эмитит `task_cancelled` audit и возвращается.

CAS на `mark_running` отбил бы повторный pickup в любом случае (фильтр
по `status='queued'`), но без этого fast-path'а audit-event был бы
generic `duplicate_dispatch` — оператору сложнее отделить штатный
cancel от gnarly multi-broker race condition.
"""

from __future__ import annotations

from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks._runner import run_task


async def _set_cancelled(task_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(
                status=TaskStatus.CANCELLED,
            )
        )
        await session.commit()


class TestCancelledHotLoopSkip:
    async def test_cancelled_task_skips_impl_and_emits_cancel_audit(
        self, make_task, fetch_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_c")
        await _set_cancelled(tid)

        called = []

        async def impl(_):
            called.append(1)
            return {}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        # impl НЕ должен быть вызван
        assert called == []

        # Status остаётся cancelled (worker не перетёр)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.CANCELLED
        # attempt не инкрементнут — mark_running не запускался
        assert t.attempt == 0
        # last_error остаётся None — мы не записываем сюда ничего
        assert t.last_error is None

        # Один audit-event со status=failure и reason=task_cancelled
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "failure"
        assert ev["allowed"] is False
        assert ev["severity"] == "WARNING"
        assert ev["target_id"] == "srv_c"
        assert ev["target_type"] == "server"
        d = ev["details"]
        assert d["reason"] == "task_cancelled"
        assert d["task_id"] == tid
        assert d["observed_status"] == "cancelled"

    async def test_cancelled_target_falls_back_to_task_id(
        self, make_task, captured_audit,
    ):
        """Cancelled task без target_server_id — target_id audit-а = task_id."""
        tid = await make_task(task_kind="power.on", target_server_id=None)
        await _set_cancelled(tid)

        async def impl(_):  # не должен запускаться
            raise AssertionError("impl must not run for cancelled task")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        assert captured_audit[0]["target_id"] == tid
        assert captured_audit[0]["details"]["reason"] == "task_cancelled"
