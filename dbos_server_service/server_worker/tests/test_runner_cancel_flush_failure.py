"""Cancel-path остаётся graceful, даже если publisher.flush_outbox падает.

`_runner.run_task` после INSERT'а в `audit_outbox` зовёт
`_safe_flush_outbox` — best-effort попытку отдать событие
loging_service'у прямо сейчас. Если publisher не смог (сетевая ошибка,
breaker open, RuntimeError в самом коде publisher'а) — это не повод
рушить cancel-обработку: row уже закоммичен в БД, фоновый
`run_publisher_loop` дотянет его позже.

Проверяем: при поднятии RuntimeError из `flush_outbox`
  * `run_task` НЕ пробрасывает исключение наверх (taskiq не должен
    видеть фейл — иначе он попытается ретраить cancel, что бессмысленно);
  * row в `audit_outbox` с action=`<service>.<verb>` и details.reason=
    `task_cancelled` всё-таки появляется и остаётся `unpublished` (фоновый
    publisher подхватит позже).
"""

from __future__ import annotations

from sqlalchemy import select, update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox, Task
from src.services import audit_outbox_publisher
from src.tasks._runner import run_task


async def _set_cancelled(task_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(
                status=TaskStatus.CANCELLED,
                cancelled_by="usr_admin",
                cancel_reason="operator stop",
            )
        )
        await session.commit()


async def _outbox_rows_for_task(task_id: str) -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AuditOutbox)
            .where(AuditOutbox.task_id == task_id)
            .order_by(AuditOutbox.id.asc())
        )
        return list((await session.execute(stmt)).scalars().all())


class TestCancelFastPathFlushFailure:
    async def test_flush_raises_cancel_audit_still_committed(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_x")
        await _set_cancelled(tid)

        flush_calls = []

        async def boom_flush():
            flush_calls.append(1)
            raise RuntimeError("publisher down")

        monkeypatch.setattr(audit_outbox_publisher, "flush_outbox", boom_flush)

        called = []

        async def impl(_):
            called.append(1)
            return {}

        # run_task НЕ должен пробросить — _safe_flush_outbox глотает.
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        # impl не дёргался (cancel fast-path), статус остался cancelled.
        assert called == []
        t = await fetch_task(tid)
        assert t.status == TaskStatus.CANCELLED

        # Попытка flush была — это и есть load-bearing assert (что мы
        # дошли до flush и graceful обработали его падение).
        assert flush_calls == [1]

        # Audit-row в БД: cancel-event закоммичен, остался unpublished
        # (фоновый publisher позже подхватит).
        rows = await _outbox_rows_for_task(tid)
        assert len(rows) == 1
        row = rows[0]
        assert row.published_at is None
        # action — то, что передали в run_task.
        assert row.payload["action"] == "server.power_on"
        assert row.payload["status"] == "failure"
        assert row.payload["severity"] == "WARNING"
        d = row.payload["details"]
        assert d["reason"] == "task_cancelled"
        assert d["task_id"] == tid
        assert d["observed_status"] == "cancelled"
        # cancel-metadata из БД доехала до payload'а.
        assert d["cancelled_by"] == "usr_admin"
        assert d["cancel_reason"] == "operator stop"
