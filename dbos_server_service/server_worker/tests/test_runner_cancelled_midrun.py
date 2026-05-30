"""Race: task cancelled while impl уже выполняется.

Сценарий:

1. `_runner.run_task` подхватил task'у, CAS перевёл в running, вышел из
   session 1 в impl.
2. Параллельно оператор дёрнул `POST /api/server/v1/tasks/{id}/cancel` —
   `tasks.status` UPDATE'нулся в `cancelled` пока impl ещё работает.
3. impl возвращается (success или exception).
4. `_runner` открывает session 2 для terminal mark_succeeded /
   mark_failed / mark_pending_for_retry.

До CAS-guard'а в `mark_*` session 2 безусловно перетирала бы статус —
cancel оператора терялся, taskiq дальше «успешно» завершал отменённую
операцию или ещё хуже — retry'ил её через back-off.

Гарантия после фикса: `mark_*` пропускают write если status уже
cancelled, возвращают None, runner логирует и пишет audit с
`reason=cancelled_midrun`. Re-kick подавляется.
"""

from __future__ import annotations

from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.repositories import task as task_repo
from src.tasks._runner import run_task


async def _cancel_in_db(task_id: str) -> None:
    """Имитирует server_service cancel-endpoint."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(status=TaskStatus.CANCELLED)
        )
        await session.commit()


class TestMarkSucceededCancelRace:
    """Happy-path impl, но cancel прошёл между running и mark_succeeded."""

    async def test_mark_succeeded_returns_none_when_cancelled(
        self, make_task,
    ):
        """Прямой unit-тест на repository-функцию."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_x")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        await _cancel_in_db(tid)

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            res = await task_repo.mark_succeeded(session, t, {"ok": True})
            await session.commit()

        assert res is None

        # БД сохранила cancelled, не перетёрла на succeeded.
        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.status == TaskStatus.CANCELLED
            assert row.result is None
            assert row.completed_at is None

    async def test_run_task_cancelled_midrun_in_impl(
        self, make_task, fetch_task, captured_audit,
    ):
        """E2E: impl возвращается успешно, но cancel прошёл во время
        выполнения. run_task не перетирает cancelled, audit идёт как
        failure/cancelled_midrun.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_y")

        impl_called: list[int] = []

        async def impl(_payload: dict) -> dict:
            impl_called.append(1)
            # Имитируем cancel оператора: пока impl «думает», statuses
            # row уже перевели в cancelled.
            await _cancel_in_db(tid)
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        assert impl_called == [1]

        row = await fetch_task(tid)
        assert row is not None
        assert row.status == TaskStatus.CANCELLED
        # result не записан — cancel победил.
        assert row.result is None
        # completed_at не выставлен — terminal-write skip'нут.
        assert row.completed_at is None

        # Audit: один event про cancelled_midrun (success-выход подавлён).
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "cancelled_midrun"
        assert ev["details"]["observed_status"] == TaskStatus.CANCELLED.value


class TestMarkFailedCancelRace:
    async def test_mark_failed_returns_none_when_cancelled(
        self, make_task,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_z")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        await _cancel_in_db(tid)

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            res = await task_repo.mark_failed(session, t, "boom")
            await session.commit()

        assert res is None

        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.status == TaskStatus.CANCELLED
            assert row.last_error is None
            assert row.completed_at is None

    async def test_run_task_impl_fails_after_cancel_terminal_path(
        self, make_task, fetch_task, captured_audit,
    ):
        """impl бросает исключение на последней попытке (max_attempts=1),
        cancel прошёл во время выполнения → mark_failed skip'нут.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_q")
        # Сводим max_attempts к 1 чтобы попасть в terminal mark_failed.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def impl(_payload: dict) -> dict:
            await _cancel_in_db(tid)
            raise RuntimeError("impl exploded")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        row = await fetch_task(tid)
        assert row is not None
        assert row.status == TaskStatus.CANCELLED
        assert row.last_error is None
        assert row.completed_at is None

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "cancelled_midrun"
        assert ev["details"]["will_retry"] is False
        assert ev["details"]["observed_status"] == TaskStatus.CANCELLED.value


class TestMarkPendingForRetryCancelRace:
    async def test_mark_pending_for_retry_returns_none_when_cancelled(
        self, make_task,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_r")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            await task_repo.mark_running(session, t, worker_id="w1")
            await session.commit()

        await _cancel_in_db(tid)

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t is not None
            res = await task_repo.mark_pending_for_retry(
                session, t, "transient ssh", scheduled_retry_at=None,
            )
            await session.commit()

        assert res is None

        # Row остался CANCELLED, не вернулся в QUEUED.
        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.status == TaskStatus.CANCELLED
            assert row.last_error is None
            # worker_id не обнулился (был назначен mark_running'ом).
            assert row.worker_id == "w1"

    async def test_run_task_impl_fails_with_retry_left_but_cancelled(
        self, make_task, fetch_task, captured_audit,
    ):
        """impl бросает, попытки ещё есть, но cancel прошёл.
        mark_pending_for_retry skip'нут, re-kick подавлен.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_s")
        # max_attempts по умолчанию (3) > 1, попадёт в retry-ветку.

        async def impl(_payload: dict) -> dict:
            await _cancel_in_db(tid)
            raise RuntimeError("transient")

        # Перехватим _schedule_retry — он не должен звать kiq для
        # cancelled mid-run task'и.
        retry_schedules: list[tuple] = []

        from src.tasks import _runner as runner_mod
        original = runner_mod._schedule_retry

        async def fake_schedule_retry(*args, **kwargs):
            retry_schedules.append((args, kwargs))
            await original(*args, **kwargs)

        runner_mod._schedule_retry = fake_schedule_retry  # type: ignore[assignment]
        try:
            await run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=impl,
            )
        finally:
            runner_mod._schedule_retry = original  # type: ignore[assignment]

        # _schedule_retry НЕ должен быть вызван — runner подавляет re-kick
        # когда mark_pending_for_retry вернул None.
        assert retry_schedules == []

        row = await fetch_task(tid)
        assert row is not None
        assert row.status == TaskStatus.CANCELLED
        # scheduled_retry_at не выставлен (запись skip'нута).
        assert row.scheduled_retry_at is None

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["reason"] == "cancelled_midrun"
        assert ev["details"]["will_retry"] is False
