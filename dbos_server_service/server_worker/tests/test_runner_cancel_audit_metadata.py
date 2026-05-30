"""Cancel-audit-event несёт `cancelled_by` и `cancel_reason` из БД.

Migration 0005 добавила колонки в `tasks` под operator-cancel: оператор
дёргает `POST /tasks/{id}/cancel?reason=...`, server_service UPDATE'ит
status=cancelled + сохраняет actor_id и reason. Раньше `_runner` писал
audit без этих полей — в loging_service приезжал event, по которому
оператору приходилось отдельно лезть в server_service за «кто и почему».

Проверяем: cancel-detection в fast-path и в success/failure midrun-ветках
добавляет `cancelled_by` / `cancel_reason` в `details` audit-payload'а.
Если поле NULL — оно не включается (пусто захламляет дашборды).
"""

from __future__ import annotations

from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks._runner import run_task


async def _cancel_with_metadata(
    task_id: str,
    *,
    cancelled_by: str | None = "usr_admin",
    cancel_reason: str | None = "operator stop",
) -> None:
    """Имитирует POST /tasks/{id}/cancel: status + cancelled_by + reason."""
    values: dict = {"status": TaskStatus.CANCELLED}
    if cancelled_by is not None:
        values["cancelled_by"] = cancelled_by
    if cancel_reason is not None:
        values["cancel_reason"] = cancel_reason
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(**values)
        )
        await session.commit()


class TestCancelFastPathMetadata:
    async def test_cancel_fast_path_audit_carries_metadata(
        self, make_task, captured_audit,
    ):
        """Cancel прошёл ДО pickup'а — fast-path audit получает поля."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_f")
        await _cancel_with_metadata(
            tid, cancelled_by="usr_op_42", cancel_reason="wrong target",
        )

        async def impl(_):
            raise AssertionError("impl must not run on cancelled task")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "task_cancelled"
        assert details["cancelled_by"] == "usr_op_42"
        assert details["cancel_reason"] == "wrong target"

    async def test_cancel_fast_path_omits_null_metadata(
        self, make_task, captured_audit,
    ):
        """Поля NULL не попадают в audit-details (никаких пустых ключей)."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_g")
        await _cancel_with_metadata(
            tid, cancelled_by=None, cancel_reason=None,
        )

        async def impl(_):
            raise AssertionError("impl must not run on cancelled task")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "task_cancelled"
        assert "cancelled_by" not in details
        assert "cancel_reason" not in details


class TestCancelMidrunMetadata:
    async def test_success_midrun_cancel_audit_carries_metadata(
        self, make_task, captured_audit,
    ):
        """Happy-path impl, cancel прошёл во время выполнения. Audit
        cancelled_midrun содержит actor + reason из refreshed row.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_h")

        async def impl(_):
            await _cancel_with_metadata(
                tid, cancelled_by="usr_op_77", cancel_reason="emergency stop",
            )
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert details["cancelled_by"] == "usr_op_77"
        assert details["cancel_reason"] == "emergency stop"

    async def test_failure_midrun_cancel_audit_carries_metadata(
        self, make_task, captured_audit,
    ):
        """impl бросает на terminal-attempt, cancel прошёл во время
        выполнения. Audit cancelled_midrun содержит actor + reason.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_i")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def impl(_):
            await _cancel_with_metadata(
                tid, cancelled_by="usr_op_99", cancel_reason="rolled back",
            )
            raise RuntimeError("impl exploded")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert details["cancelled_by"] == "usr_op_99"
        assert details["cancel_reason"] == "rolled back"


class TestCancelClockPair:
    """Cancel-audit несёт пару clock-меток для устойчивости к NTP-drift'у.

    `cancel_request_received_at` — server_service'ский clock (= `task.cancelled_at`),
    дублирует значение, которое уже идёт в `payload.timestamp` через
    `_cancel_timestamp`. `worker_clock_now` — worker'ский UTC в момент сборки
    audit-event'а. По паре SIEM видит дрейф часов между server и worker.
    """

    async def test_fast_path_emits_both_clocks(self, make_task, captured_audit):
        from datetime import datetime, timezone

        tid = await make_task(task_kind="power.on", target_server_id="srv_clk")
        cancelled_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(
                    status=TaskStatus.CANCELLED,
                    cancelled_by="usr_op_1",
                    cancel_reason="reroute",
                    cancelled_at=cancelled_at,
                )
            )
            await session.commit()

        async def impl(_):
            raise AssertionError("impl must not run")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert "cancel_request_received_at" in details
        assert "worker_clock_now" in details
        # Оба — валидные ISO-8601 UTC строки.
        datetime.fromisoformat(details["cancel_request_received_at"])
        datetime.fromisoformat(details["worker_clock_now"])

    async def test_midrun_success_emits_both_clocks(
        self, make_task, captured_audit,
    ):
        from datetime import datetime, timezone

        tid = await make_task(task_kind="power.on", target_server_id="srv_clk2")

        async def impl(_):
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Task).where(Task.id == tid).values(
                        status=TaskStatus.CANCELLED,
                        cancelled_by="usr_op_2",
                        cancel_reason="stop",
                        cancelled_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["reason"] == "cancelled_midrun"
        assert "cancel_request_received_at" in details
        assert "worker_clock_now" in details
