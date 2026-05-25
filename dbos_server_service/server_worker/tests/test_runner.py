"""Тесты универсального `_runner.run_task`.

* happy: load → mark_running (attempt++) → impl → mark_succeeded → audit success;
* failure: impl бросает → mark_failed с f"{Type}: {message}" в last_error,
  audit emit с status=failure;
* task_not_found: audit success/failure без mark_running.

После outbox-рефакторинга audit идёт через `audit_outbox` таблицу. `captured_audit` fixture мокает
`audit_client.emit` — то есть события всё равно «приедут» в список после
успешного `flush_outbox`, но между commit'ом transactional outbox и
сетевым emit'ом существует обозримый зазор. Все тесты в этом файле
проверяют состояние ПОСЛЕ `run_task` — на этот момент `_safe_flush_outbox`
уже отработал.

Whitelist details.result: handler-агностические тесты в этом файле
*не* объявляют `audit_safe_fields`. Это значит, в audit details.result
лежит sentinel `{"emitted": False, "reason": "no_whitelist"}` (см.
`_filter_result_for_audit`). Тесты на whitelist — в `test_audit_outbox.py`.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks._runner import run_task


# ── Happy path ───────────────────────────────────────────────────────────────

class TestRunTaskHappy:
    async def test_marks_running_then_succeeded(self, make_task, fetch_task, captured_audit):
        tid = await make_task(task_kind="power.on", payload={"server_id": "srv_1"})

        captured_payloads = []

        async def impl(payload):
            captured_payloads.append(payload)
            # На момент impl() в БД task в RUNNING — проверим в отдельной сессии
            t = await fetch_task(tid)
            assert t.status == TaskStatus.RUNNING
            assert t.attempt == 1
            assert t.started_at is not None
            return {"power_state": "on"}

        await run_task(tid, audit_action="server.power_on",
                       audit_target_type="server", impl=impl)

        # Финальное состояние
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}
        assert t.completed_at is not None
        assert t.last_error is None

        # impl получил payload
        assert captured_payloads == [{"server_id": "srv_1"}]

        # audit-событие success с правильным actor/target/request_id
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "success"
        assert ev["allowed"] is True
        assert ev["target_id"] == "srv_test"  # = target_server_id (default fixture)
        assert ev["target_type"] == "server"
        assert ev["request_id"] == "req_test"
        assert ev["actor_id"] == "usr_caller"
        assert ev["details"]["task_id"] == tid
        # handler в этом тесте НЕ объявил audit_safe_fields → результат
        # маскируется sentinel'ом «по дефолту секреты не уходят».
        assert ev["details"]["result"] == {
            "emitted": False,
            "reason": "no_whitelist",
        }

    async def test_falls_back_to_task_id_when_no_target_server(
        self, make_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on", target_server_id=None)
        async def impl(_): return {}
        await run_task(tid, audit_action="server.power_on", impl=impl)
        # target_id audit-события = task_id, поскольку target_server_id is None
        assert captured_audit[0]["target_id"] == tid

    async def test_result_can_be_none(self, make_task, fetch_task, captured_audit):
        tid = await make_task()
        async def impl(_): return None
        await run_task(tid, audit_action="x.action", impl=impl)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result is None
        assert captured_audit[0]["details"]["result"] is None

    async def test_attempt_increments_from_zero(self, make_task, fetch_task, captured_audit):
        tid = await make_task()
        async def impl(_): return {"ok": True}
        await run_task(tid, audit_action="x", impl=impl)
        t = await fetch_task(tid)
        assert t.attempt == 1  # 0 → 1


# ── Failure path ─────────────────────────────────────────────────────────────
#
# `_runner` при exception в impl делает `mark_pending_for_retry` если
# `attempt < max_attempts`, и terminal `mark_failed` только при исчерпании
# попыток. Чтобы существующие тесты фиксировали именно terminal failed,
# передаём `max_attempts=1` через фикстуру `make_task`. Тесты на
# retry-семантику — в `test_p1_retry_and_shutdown.py::TestRetryOnFailure`.
# `_schedule_retry` мокается на no-op чтобы не утекать background-task'и
# в тесты.


@pytest.fixture
def _no_retry_schedule(monkeypatch):
    """Заглушка для `_schedule_retry` — иначе при `max_attempts > 1`
    фейловые тесты порождают background-task'у с asyncio.sleep(10s+) и
    pytest-asyncio её ждёт при teardown."""
    from src.tasks import _runner

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(_runner, "_schedule_retry", noop)


class TestRunTaskFailure:
    async def test_exception_in_impl_marks_failed(
        self, make_task, fetch_task, captured_audit, _no_retry_schedule,
    ):
        # max_attempts=1 → первая ошибка сразу terminal FAILED (retry-семантика
        # покрыта отдельно в test_p1_retry_and_shutdown.py).
        tid = await _make_task_with_max_attempts(make_task, max_attempts=1)

        async def boom(_):
            raise RuntimeError("Boom!")

        await run_task(tid, audit_action="server.power_on",
                       audit_target_type="server", impl=boom)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error == "RuntimeError: Boom!"
        assert t.completed_at is not None
        # result остаётся None
        assert t.result is None

        # audit с status=failure, severity=ERROR
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "failure"
        assert ev["allowed"] is False
        assert ev["severity"] == "ERROR"
        assert "Boom!" in ev["details"]["error"]
        assert ev["details"]["task_id"] == tid

    async def test_keyerror_in_impl_marks_failed_with_class_name(
        self, make_task, fetch_task, captured_audit, _no_retry_schedule,
    ):
        # payload без `server_id` (truthy чтобы make_task не подставил default)
        tid = await _make_task_with_max_attempts(
            make_task, max_attempts=1, payload={"other_key": 1},
        )

        async def need_server_id(payload):
            return payload["server_id"]  # → KeyError

        await run_task(tid, audit_action="x", impl=need_server_id)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "KeyError" in t.last_error

    async def test_attempt_still_increments_on_failure(
        self, make_task, fetch_task, captured_audit, _no_retry_schedule,
    ):
        tid = await _make_task_with_max_attempts(make_task, max_attempts=1)
        async def boom(_):
            raise ValueError("nope")
        await run_task(tid, audit_action="x", impl=boom)
        t = await fetch_task(tid)
        assert t.attempt == 1


async def _make_task_with_max_attempts(
    make_task, *, max_attempts: int, payload: dict | None = None,
) -> str:
    """Helper: `make_task` фиксирует max_attempts через ORM-default.

    Эти тесты хотят terminal-failure, поэтому max_attempts=1. Сам
    `make_task` не принимает max_attempts (был придуман до retry-фикса);
    переопределяем поле прямой UPDATE-командой после insert'а.
    """
    from sqlalchemy import update

    from src.db.session import AsyncSessionLocal
    from src.models import Task

    tid = await make_task(payload=payload)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=max_attempts)
        )
        await session.commit()
    return tid


# ── Task not found ───────────────────────────────────────────────────────────

class TestRunTaskMissing:
    async def test_unknown_task_id_audit_only(self, captured_audit, fetch_task):
        async def impl(_):  # не должен быть вызван
            raise AssertionError("impl must not run for missing task")

        await run_task(
            "tsk_phantom_id",
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        # task в БД нет → fetch_task вернёт None
        assert await fetch_task("tsk_phantom_id") is None

        # одно audit-событие, status=failure, reason=task_not_found
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "failure"
        assert ev["allowed"] is False
        assert ev["target_id"] == "tsk_phantom_id"
        assert ev["target_type"] == "task"
        assert ev["details"]["reason"] == "task_not_found"
        assert ev["severity"] == "ERROR"
