"""Тесты CAS / retry / shutdown / scheduler для server_worker.

Покрывает:

1. **mark_running CAS** — повторный `run_task` на уже-terminal task'у
   не сбрасывает её статус, impl не вызывается, audit пишет
   «duplicate_dispatch».

2. **Retry / back-off** — при exception в impl, если
   `attempt < max_attempts`, task → `queued` + schedule re-kick через
   exp back-off. При исчерпании попыток — `mark_failed` как раньше.

3. **Graceful shutdown** — `_drain_running_tasks` ждёт активные task'и
   до timeout, после mark_pending_for_retry/mark_failed зависших
   + audit «worker_shutdown».

4. **Scheduler skeleton** — `scheduler` экспортируется на module-level,
   имеет правильную сигнатуру для `taskiq scheduler src.main:scheduler`,
   регистрирует `system.heartbeat`.

Все тесты — против реальной PostgreSQL (БД из conftest), без моков БД.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.repositories import task as task_repo


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ── helpers ─────────────────────────────────────────────────────────────────

async def _all_outbox_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ mark_running CAS                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestMarkRunningCAS:
    """Проверяем что `mark_running` — atomic compare-and-swap по статусу.

    Регрессия: повторный enqueue (server_service
    dispatch_task retry или Redis re-delivery) раньше сбрасывал
    `succeeded`/`failed` обратно в `running` и worker запускал impl
    повторно.
    """

    async def test_mark_running_on_queued_succeeds(self, make_task, fetch_task):
        """Базовый кейс: queued → running, attempt инкрементирован."""
        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            assert t.status == TaskStatus.QUEUED
            assert t.attempt == 0

            marked = await task_repo.mark_running(session, t)
            await session.commit()

        assert marked is not None
        assert marked.status == TaskStatus.RUNNING
        assert marked.attempt == 1

        # И в БД сохранилось.
        t = await fetch_task(tid)
        assert t.status == TaskStatus.RUNNING
        assert t.attempt == 1
        assert t.started_at is not None

    async def test_mark_running_on_succeeded_returns_none(
        self, make_task, fetch_task,
    ):
        """Если task уже SUCCEEDED — CAS не сработает, статус не меняется."""
        tid = await make_task(task_kind="power.on")
        # Преднамеренно вручную переведём в SUCCEEDED.
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_succeeded(session, t, {"power_state": "on"})
            await session.commit()

        t_before = await fetch_task(tid)
        assert t_before.status == TaskStatus.SUCCEEDED
        original_attempt = t_before.attempt
        original_result = t_before.result

        # Попытка mark_running на terminal — отказ.
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            marked = await task_repo.mark_running(session, t)
            await session.commit()

        assert marked is None, "CAS должен отказать на SUCCEEDED"

        # БД не должна была измениться.
        t_after = await fetch_task(tid)
        assert t_after.status == TaskStatus.SUCCEEDED
        assert t_after.attempt == original_attempt
        assert t_after.result == original_result

    async def test_mark_running_on_failed_returns_none(self, make_task, fetch_task):
        """Аналогично для FAILED — terminal означает 'не трогать'."""
        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_failed(session, t, "boom")
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            marked = await task_repo.mark_running(session, t)
            await session.commit()

        assert marked is None

        t_after = await fetch_task(tid)
        assert t_after.status == TaskStatus.FAILED
        assert t_after.last_error == "boom"

    async def test_mark_running_on_already_running_returns_none(
        self, make_task, fetch_task,
    ):
        """Idempotency: уже-running тоже не двигается (защита от race
        двух worker'ов на одном task_id)."""
        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            first = await task_repo.mark_running(session, t)
            await session.commit()
        assert first is not None
        assert first.attempt == 1

        # Вторая попытка mark_running — отказ.
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            second = await task_repo.mark_running(session, t)
            await session.commit()
        assert second is None

        t_after = await fetch_task(tid)
        assert t_after.status == TaskStatus.RUNNING
        # attempt НЕ инкрементирован вторым вызовом — это критично.
        assert t_after.attempt == 1


class TestRunTaskCASRejection:
    """`run_task` лишь распаковывает CAS rejection в audit «duplicate_dispatch».

    Регрессия: до фикса второй `run_task(tid)` на уже-succeeded task'у:
    (а) запускал impl (могло re-power-on сервер),
    (б) сбрасывал status в running, обнулял last_error.
    """

    async def test_duplicate_dispatch_does_not_invoke_impl(
        self, make_task, fetch_task, captured_audit,
    ):
        from src.tasks._runner import run_task

        tid = await make_task(task_kind="power.on")
        # Перевод в SUCCEEDED руками — симулируем «прошлый прогон» успешный.
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_succeeded(session, t, {"power_state": "on"})
            await session.commit()

        invoked = []

        async def impl(_payload):
            invoked.append(1)
            return {"power_state": "off"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        # impl НЕ вызвался.
        assert invoked == []

        # Status в БД остался SUCCEEDED, не сбросился на RUNNING.
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}

        # Audit получил «duplicate_dispatch».
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "failure"
        assert ev["details"]["reason"] == "duplicate_dispatch"
        assert ev["details"]["observed_status"] == TaskStatus.SUCCEEDED
        assert ev["severity"] == "WARNING"

    async def test_duplicate_dispatch_writes_outbox_row(
        self, make_task, fetch_task, captured_audit,
    ):
        """Outbox-row создан → след в audit_outbox остался даже если
        loging_service был недоступен."""
        from src.tasks._runner import run_task

        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            await task_repo.mark_succeeded(session, t, {})
            await session.commit()

        async def impl(_):
            return {}

        await run_task(tid, audit_action="server.power_on", impl=impl)

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].payload["details"]["reason"] == "duplicate_dispatch"


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ retry / back-off                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestRetryOnFailure:
    """`_runner` при exception в impl должен:

    * `attempt < max_attempts` → status QUEUED, re-kick через back-off;
    * `attempt >= max_attempts` → status FAILED.

    Re-kick идёт через `broker.find_task(kind).kicker().kiq(task_id)` в
    отдельной `asyncio.create_task` после sleep'а. В тестах мокаем
    `_schedule_retry` чтобы не ждать back-off (и чтобы не ходить в Redis).
    """

    async def test_failure_with_attempts_left_marks_pending_for_retry(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from src.tasks import _runner

        tid = await make_task(task_kind="power.on")

        # max_attempts=3 (дефолт). После 1-го фейла → attempt=1, status=queued.
        scheduled = []

        # _schedule_retry принимает kwarg `delay` — фикстура берёт
        # любые kwargs чтобы не упасть на изменении сигнатуры.
        async def fake_schedule(kind, task_id, attempt, **kwargs):
            scheduled.append((kind, task_id, attempt))

        monkeypatch.setattr(_runner, "_schedule_retry", fake_schedule)

        async def boom(_):
            raise RuntimeError("transient")

        await _runner.run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=boom,
        )

        t = await fetch_task(tid)
        # КРИТИЧНО: status вернулся в QUEUED, не остался FAILED.
        assert t.status == TaskStatus.QUEUED
        assert t.attempt == 1
        assert t.last_error is not None
        assert "RuntimeError" in t.last_error
        # completed_at НЕ выставлен (task не завершён).
        assert t.completed_at is None

        # _schedule_retry был вызван с правильными аргументами.
        assert scheduled == [("power.on", tid, 1)]

        # Audit-event: status=failure, severity=WARNING, will_retry=True.
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["severity"] == "WARNING"
        assert ev["details"]["will_retry"] is True
        assert ev["details"]["attempt"] == 1
        assert ev["details"]["max_attempts"] == 3

    async def test_failure_after_max_attempts_marks_failed_terminal(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Третий фейл (attempt уже был 2 в БД, +1 в mark_running = 3 == max)
        переводит task'у в terminal FAILED."""
        from src.tasks import _runner

        # Создаём task с attempt=2 (уже было 2 фейла), max_attempts=3.
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_test",
                "payload": {"server_id": "srv_test"},
                "attempt": 2,
                "max_attempts": 3,
                "created_by": "usr_caller",
                "request_id": "req_test",
            })
            await session.commit()

        scheduled = []

        async def fake_schedule(kind, task_id, attempt, **kwargs):
            scheduled.append((kind, task_id, attempt))

        monkeypatch.setattr(_runner, "_schedule_retry", fake_schedule)

        async def boom(_):
            raise RuntimeError("3rd fail")

        await _runner.run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=boom,
        )

        t = await fetch_task(tid)
        # mark_running инкрементировал attempt: 2 → 3. attempt == max_attempts,
        # дальше retry не делаем.
        assert t.attempt == 3
        assert t.status == TaskStatus.FAILED
        assert t.completed_at is not None
        # re-kick не делается.
        assert scheduled == []

        # Audit: severity=ERROR, will_retry=False.
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["severity"] == "ERROR"
        assert ev["details"]["will_retry"] is False
        assert ev["details"]["attempt"] == 3
        assert ev["details"]["max_attempts"] == 3

    async def test_max_attempts_one_falls_through_immediately(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`max_attempts=1` — first failure уже terminal."""
        from src.tasks import _runner

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_test",
                "payload": {"server_id": "srv_test"},
                "attempt": 0,
                "max_attempts": 1,
            })
            await session.commit()

        scheduled = []
        monkeypatch.setattr(
            _runner,
            "_schedule_retry",
            lambda *a, **k: scheduled.append(a),
        )

        async def boom(_):
            raise ValueError("nope")

        await _runner.run_task(tid, audit_action="x", impl=boom)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.attempt == 1
        assert scheduled == []

    async def test_happy_path_does_not_schedule_retry(
        self, make_task, fetch_task, monkeypatch,
    ):
        """Success — retry-machinery не дёргается."""
        from src.tasks import _runner

        tid = await make_task(task_kind="power.on")

        scheduled = []
        monkeypatch.setattr(
            _runner,
            "_schedule_retry",
            lambda *a, **k: scheduled.append(a),
        )

        async def impl(_):
            return {"power_state": "on"}

        await _runner.run_task(tid, audit_action="server.power_on", impl=impl)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert scheduled == []


class TestScheduleRetryBackoff:
    """`_schedule_retry` (отдельная background-task'а) должна:

    * посчитать back-off = base * 2^(attempt-1) с потолком,
    * после sleep'а — найти broker-task по kind и вызвать kiq(task_id).

    Не тестируем сам sleep (слишком долго), вместо этого monkeypatch'им
    asyncio.sleep чтобы видеть delay-аргумент.
    """

    async def test_backoff_grows_exponentially(self, monkeypatch):
        """attempt=1 → 10s, attempt=2 → 20s, attempt=3 → 40s."""
        from src.tasks import _runner

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        # Мокаем broker.find_task чтобы не упасть на nonexistent kind.
        fake_kicked = []

        class FakeKicker:
            async def kiq(self, *args, **kwargs):
                fake_kicked.append(args)

        class FakeTask:
            def kicker(self):
                return FakeKicker()

        from src import main
        monkeypatch.setattr(main.broker, "find_task", lambda kind: FakeTask())
        monkeypatch.setattr(_runner.asyncio, "sleep", fake_sleep)

        # asyncio.create_task внутри _schedule_retry — нам нужно дождаться.
        # Получим handle и await'нем.
        original_create_task = asyncio.create_task
        created = []

        def capture_create_task(coro, **kw):
            t = original_create_task(coro, **kw)
            created.append(t)
            return t

        monkeypatch.setattr(_runner.asyncio, "create_task", capture_create_task)

        for attempt, expected in [(1, 10.0), (2, 20.0), (3, 40.0)]:
            await _runner._schedule_retry("power.on", "tsk_x", attempt)

        # Дожидаемся background tasks.
        for t in created:
            await t

        assert sleeps == [10.0, 20.0, 40.0]
        # И каждый — kiq'нул task_id "tsk_x".
        assert len(fake_kicked) == 3

    async def test_backoff_capped_at_max(self, monkeypatch):
        """Большой attempt → delay capped at 300s."""
        from src.tasks import _runner

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        class FakeKicker:
            async def kiq(self, *args, **kwargs):
                pass

        class FakeTask:
            def kicker(self):
                return FakeKicker()

        from src import main
        monkeypatch.setattr(main.broker, "find_task", lambda kind: FakeTask())
        monkeypatch.setattr(_runner.asyncio, "sleep", fake_sleep)

        original_create_task = asyncio.create_task
        created = []

        def capture_create_task(coro, **kw):
            t = original_create_task(coro, **kw)
            created.append(t)
            return t

        monkeypatch.setattr(_runner.asyncio, "create_task", capture_create_task)

        # 10 * 2^9 = 5120s, должно быть capped к 300s.
        await _runner._schedule_retry("power.on", "tsk_x", 10)
        for t in created:
            await t

        assert sleeps == [300.0]

    async def test_unknown_task_kind_skips_kick_no_raise(self, monkeypatch):
        """Если broker не знает task_kind (миграция вырезала kind) —
        не падаем, просто логируем."""
        from src.tasks import _runner

        async def fake_sleep(d):
            pass

        from src import main
        monkeypatch.setattr(main.broker, "find_task", lambda kind: None)
        monkeypatch.setattr(_runner.asyncio, "sleep", fake_sleep)

        original_create_task = asyncio.create_task
        created = []

        def capture_create_task(coro, **kw):
            t = original_create_task(coro, **kw)
            created.append(t)
            return t

        monkeypatch.setattr(_runner.asyncio, "create_task", capture_create_task)

        # Не должна бросить.
        await _runner._schedule_retry("nonexistent.kind", "tsk_x", 1)
        for t in created:
            await t  # тоже не должна бросить


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ Graceful shutdown                                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestGracefulShutdownDrain:
    """`_drain_running_tasks` (taskiq WORKER_SHUTDOWN hook) обрабатывает
    оставшиеся `running` task'и: mark_pending_for_retry / mark_failed +
    audit «worker_shutdown»."""

    async def test_no_running_tasks_returns_quickly(self, monkeypatch):
        """RUNNING_TASKS пуст — drain выходит без работы."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        RUNNING_TASKS.clear()

        state = TaskiqState()
        await _drain_running_tasks(state)  # без исключения

    async def test_running_task_with_retry_left_marks_pending(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Active running task'у с attempt < max_attempts при таймауте
        переводят в QUEUED для retry следующим worker'ом."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        # Создаём task в RUNNING state с attempt=1, max_attempts=3.
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_test",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
                "created_by": "usr_caller",
                "request_id": "req_test",
            })
            await session.commit()

        # Симулируем «зависший handler» — task_id в RUNNING_TASKS, impl
        # никогда не разойдётся сама.
        RUNNING_TASKS.add(tid)

        # Уменьшаем shutdown timeout до 0.5s — иначе тест медленный.
        # get_settings() кеширован, патчим прямо в инстансе.
        from src.main import _settings as main_settings
        monkeypatch.setattr(
            main_settings, "worker_shutdown_timeout_seconds", 0.5
        )

        state = TaskiqState()
        await _drain_running_tasks(state)

        # Task должна быть QUEUED (для retry).
        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED
        assert "worker_shutdown" in (t.last_error or "")
        # attempt не трогаем (его инкрементирует следующий mark_running).
        assert t.attempt == 1

        # Audit-event записан.
        outbox = await _all_outbox_rows()
        assert len(outbox) == 1
        payload = outbox[0].payload
        assert payload["action"] == "task.worker_shutdown"
        assert payload["details"]["reason"] == "worker_shutdown"
        assert payload["details"]["will_retry"] is True
        assert payload["severity"] == "WARNING"

        # RUNNING_TASKS освобождён.
        assert tid not in RUNNING_TASKS

    async def test_running_task_at_max_attempts_marks_failed(
        self, fetch_task, captured_audit, monkeypatch,
    ):
        """attempt >= max_attempts → mark_failed (terminal), audit ERROR."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_test",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 3,
                "max_attempts": 3,
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(
            main_settings, "worker_shutdown_timeout_seconds", 0.5
        )

        state = TaskiqState()
        await _drain_running_tasks(state)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "worker_shutdown" in (t.last_error or "")

        outbox = await _all_outbox_rows()
        assert outbox[0].payload["details"]["will_retry"] is False
        assert outbox[0].payload["severity"] == "ERROR"

    async def test_task_finishing_naturally_during_wait_is_not_touched(
        self, fetch_task, monkeypatch,
    ):
        """Если impl завершился (RUNNING_TASKS.discard) до таймаута —
        drain не должен ничего менять."""
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_test",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(
            main_settings, "worker_shutdown_timeout_seconds", 2.0
        )

        # В отдельной таске «доделаем» impl через 0.3s — переведём в
        # SUCCEEDED и уберём из RUNNING_TASKS.
        async def finish_impl():
            await asyncio.sleep(0.3)
            async with AsyncSessionLocal() as session:
                t = await task_repo.get_by_id(session, tid)
                await task_repo.mark_succeeded(session, t, {"power_state": "on"})
                await session.commit()
            RUNNING_TASKS.discard(tid)

        finisher = asyncio.create_task(finish_impl())

        state = TaskiqState()
        await _drain_running_tasks(state)
        await finisher

        # Task осталась SUCCEEDED, drain её не тронул.
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ Scheduler skeleton                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestSchedulerWiring:
    """`scheduler` объект должен существовать в `src.main` и быть
    готовым к запуску через `taskiq scheduler src.main:scheduler`.

    Сам факт запуска scheduler-процесса в unit-тестах не воспроизводим
    (нужна отдельная команда + Redis). Тесты сосредоточены на
    «infrastructure ready»: scheduler есть, source — LabelScheduleSource,
    привязан к тому же broker'у.
    """

    def test_scheduler_exported(self):
        from src.main import broker, scheduler
        from taskiq import TaskiqScheduler

        assert isinstance(scheduler, TaskiqScheduler)
        assert scheduler.broker is broker

    def test_scheduler_uses_label_schedule_source(self):
        """LabelScheduleSource = чтение `schedule` label'ов с broker.task()."""
        from src.main import scheduler
        from taskiq.schedule_sources import LabelScheduleSource

        assert len(scheduler.sources) >= 1
        assert any(isinstance(s, LabelScheduleSource) for s in scheduler.sources)

    def test_heartbeat_task_registered(self):
        """`system.heartbeat` должна быть в broker.get_all_tasks(),
        чтобы scheduler мог её kiq'нуть и worker — выполнить."""
        from src.main import broker

        assert "system.heartbeat" in broker.get_all_tasks()


class TestSchedulerHeartbeatLabel:
    """Когда `SCHEDULER_ENABLED=true`, `system.heartbeat` должна иметь
    `schedule` label с cron-выражением (LabelScheduleSource поднимет
    её на startup'е scheduler-процесса).

    Поскольку Settings кешируется и читается на import-time `src.main`,
    эти тесты опираются на текущее значение flag'а (дефолт False в
    test'ах). Тестируем именно стабильную часть: задача регистрируется
    как task, а наличие cron'а зависит от env."""

    async def test_heartbeat_callable_does_not_raise(self):
        """Heartbeat-handler должен исполняться без исключений (это
        no-op `logger.info`)."""
        from src.main import system_heartbeat

        # taskiq decorator оборачивает функцию, оригинал доступен через
        # `original_func` (см. test_task_handlers.py).
        if hasattr(system_heartbeat, "original_func"):
            await system_heartbeat.original_func()
        else:
            # fallback — это уже raw coroutine.
            await system_heartbeat()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ retry-task strong-ref (защита от GC fire-and-forget)                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestScheduleRetryStrongRef:
    """`_schedule_retry` должна держать сильную ссылку на фоновую task'у.

    Регрессия: `asyncio.create_task(...)` без сохранения handle — event
    loop держит лишь weakref, на длинном back-off (20s+) GC мог собрать
    task'у до её пробуждения → in-process re-kick молча не происходил.
    Фикс: модульный set `_RETRY_TASKS` + `add_done_callback(discard)`.
    """

    async def test_retry_task_held_in_module_set_until_done(self, monkeypatch):
        """Пока retry-task спит на back-off, её handle лежит в _RETRY_TASKS;
        после завершения done-callback его снимает."""
        from src.tasks import _runner

        _runner._RETRY_TASKS.clear()

        release = asyncio.Event()

        async def fake_sleep(_delay):
            # Держим task в «спящем» состоянии до явного release.
            await release.wait()

        kicked = []

        class FakeKicker:
            async def kiq(self, *args, **kwargs):
                kicked.append(args)

        class FakeTask:
            def kicker(self):
                return FakeKicker()

        from src import main
        monkeypatch.setattr(main.broker, "find_task", lambda kind: FakeTask())
        monkeypatch.setattr(_runner.asyncio, "sleep", fake_sleep)

        await _runner._schedule_retry("power.on", "tsk_ref", 1, delay=20.0)

        # Handle сохранён, на него есть сильная ссылка из set'а.
        assert len(_runner._RETRY_TASKS) == 1
        held = next(iter(_runner._RETRY_TASKS))
        assert held.get_name() == "retry_tsk_ref"
        assert not held.done()

        # Пробуждаем — task доходит до kiq и завершается.
        release.set()
        await held

        # done-callback снял ссылку.
        assert held not in _runner._RETRY_TASKS
        assert len(_runner._RETRY_TASKS) == 0
        assert len(kicked) == 1

    async def test_retry_task_discarded_even_on_error(self, monkeypatch):
        """Если delayed-kick падает внутри (broker бросил) — handle всё
        равно снимается по done-callback (set не течёт)."""
        from src.tasks import _runner

        _runner._RETRY_TASKS.clear()

        async def fake_sleep(_delay):
            return

        def boom_find(_kind):
            raise RuntimeError("broker down")

        from src import main
        monkeypatch.setattr(main.broker, "find_task", boom_find)
        monkeypatch.setattr(_runner.asyncio, "sleep", fake_sleep)

        await _runner._schedule_retry("power.on", "tsk_err", 1, delay=1.0)
        assert len(_runner._RETRY_TASKS) == 1
        held = next(iter(_runner._RETRY_TASKS))

        # _delayed_kick глотает Exception (fire-and-forget), но task всё
        # равно завершится — done-callback должен снять ссылку.
        await held
        assert held not in _runner._RETRY_TASKS
        assert len(_runner._RETRY_TASKS) == 0


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ RUNNING_TASKS регистрация до commit'а mark_running (drain-окно)          ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestRunningTaskRegisteredBeforeCommit:
    """`run_task` должна класть task_id в RUNNING_TASKS ДО commit'а
    mark_running.

    Регрессия: регистрация шла ПОСЛЕ закрытия session 1 → в узком окне
    «commit прошёл, impl ещё не начат» SIGTERM-drain не видел задачу в
    RUNNING_TASKS и она зависала `running` навсегда (до orphan-sweep'а).
    """

    async def test_id_present_in_running_tasks_at_commit_time(
        self, make_task, fetch_task, monkeypatch,
    ):
        """В момент commit'а mark_running task_id уже в RUNNING_TASKS."""
        from src.tasks import _runner
        from src.tasks._runner_state import RUNNING_TASKS
        from src.db import session as session_mod

        tid = await make_task(task_kind="power.on")

        observed: dict[str, bool] = {}

        # Оборачиваем AsyncSession.commit чтобы поймать членство в множестве
        # ровно на первом commit'е (это commit session 1 — mark_running).
        real_commit = session_mod.AsyncSession.commit
        first_seen = []

        async def spy_commit(self):
            if not first_seen:
                first_seen.append(True)
                observed["in_set_before_first_commit"] = tid in RUNNING_TASKS
            return await real_commit(self)

        monkeypatch.setattr(session_mod.AsyncSession, "commit", spy_commit)

        async def impl(_):
            # На входе в impl задача тоже должна быть в множестве.
            observed["in_set_during_impl"] = tid in RUNNING_TASKS
            return {"power_state": "on"}

        await _runner.run_task(tid, audit_action="server.power_on", impl=impl)

        assert observed.get("in_set_before_first_commit") is True
        assert observed.get("in_set_during_impl") is True

        # После терминального статуса множество очищено (нет утечки).
        assert tid not in RUNNING_TASKS
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

    async def test_drain_in_window_sees_task_and_finalizes(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Симулируем drain в окне «mark_running закоммичен, impl висит»:
        задача уже в RUNNING_TASKS, drain её видит и переводит в QUEUED."""
        from src.tasks import _runner
        from src.tasks._runner_state import RUNNING_TASKS
        from src.main import _drain_running_tasks
        from src.main import _settings as main_settings
        from taskiq import TaskiqState

        tid = await make_task(task_kind="power.on")

        impl_entered = asyncio.Event()
        let_impl_finish = asyncio.Event()

        async def hanging_impl(_):
            impl_entered.set()
            # Висим, пока drain не отработает.
            await let_impl_finish.wait()
            return {"power_state": "on"}

        runner_coro = asyncio.create_task(
            _runner.run_task(tid, audit_action="server.power_on", impl=hanging_impl)
        )

        # Ждём, пока impl стартует — к этому моменту mark_running закоммичен
        # и task_id уже зарегистрирован.
        await asyncio.wait_for(impl_entered.wait(), timeout=5.0)
        assert tid in RUNNING_TASKS

        # БД уже видит running.
        t = await fetch_task(tid)
        assert t.status == TaskStatus.RUNNING

        # Запускаем drain с коротким таймаутом — он должен увидеть задачу.
        monkeypatch.setattr(main_settings, "worker_shutdown_timeout_seconds", 0.5)
        await _drain_running_tasks(TaskiqState())

        # Drain финализировал задачу (attempt=1 < max=3 → QUEUED для retry).
        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED
        assert "worker_shutdown" in (t.last_error or "")
        assert tid not in RUNNING_TASKS

        # Отпускаем impl — run_task попытается mark_succeeded на уже-QUEUED
        # row'е (get_by_id вернёт row, mark_succeeded перепишет статус).
        # Нас интересует, что drain отработал в окне; финализацию runner'а
        # просто дожидаемся без падения.
        let_impl_finish.set()
        await runner_coro

        # Регистрация снята после завершения runner'а в любом случае.
        assert tid not in RUNNING_TASKS
