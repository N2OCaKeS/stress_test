"""Smoke tests — module imports, taskiq broker registers all tasks."""

from __future__ import annotations

import asyncio

import pytest


def test_models_import_and_register():
    from src.db.base import Base
    from src.models import AuditOutbox, Task

    assert Task is not None
    assert AuditOutbox is not None
    assert "tasks" in Base.metadata.tables
    assert "audit_outbox" in Base.metadata.tables


def test_broker_imports_and_has_all_tasks():
    """Importing main + tasks must register every task on the broker."""
    from src.main import broker

    expected = {
        "power.on",
        "power.off",
        "power.reboot",
        "power.status",
        "inventory.sync",
        "account.rotate_password",
        "ipmi.rotate_password",
    }
    registered = set(broker.get_all_tasks().keys())
    missing = expected - registered
    assert not missing, f"task names not registered: {missing}"


def test_task_kind_enum_matches_broker():
    """Every TaskKind enum value should have a matching broker task name."""
    from src.core.constants import TaskKind
    from src.main import broker

    registered = set(broker.get_all_tasks().keys())
    for kind in TaskKind:
        assert kind.value in registered, f"{kind.value} missing from broker"


# ── Audit outbox publisher: startup/shutdown wiring ─────────────────────────
#
# `run_publisher_loop` написан, но раньше не подключался —
# `audit_outbox`-строки копились при недоступности loging_service. Теперь
# `main.py` регистрирует startup/shutdown хуки на taskiq broker'е. Эти тесты
# фиксируют сам факт регистрации хуков + поведение (создание/отмена asyncio.Task).


def test_publisher_startup_hook_registered():
    """WORKER_STARTUP event должен иметь хотя бы один зарегистрированный handler."""
    from taskiq import TaskiqEvents

    from src.main import broker

    handlers = broker.event_handlers[TaskiqEvents.WORKER_STARTUP]
    assert handlers, "no WORKER_STARTUP handlers registered"
    names = {getattr(h, "__name__", "") for h in handlers}
    assert "_start_audit_outbox_publisher" in names, (
        f"audit_outbox publisher startup hook missing, got: {names}"
    )


def test_publisher_shutdown_hook_registered():
    """WORKER_SHUTDOWN event должен иметь хотя бы один зарегистрированный handler."""
    from taskiq import TaskiqEvents

    from src.main import broker

    handlers = broker.event_handlers[TaskiqEvents.WORKER_SHUTDOWN]
    assert handlers, "no WORKER_SHUTDOWN handlers registered"
    names = {getattr(h, "__name__", "") for h in handlers}
    assert "_stop_audit_outbox_publisher" in names, (
        f"audit_outbox publisher shutdown hook missing, got: {names}"
    )


async def test_publisher_startup_creates_background_task(monkeypatch):
    """Startup-хук должен закинуть `run_publisher_loop` в `asyncio.create_task`
    и сохранить хэндл в `state`.

    Чтобы реальный loop не дёргал БД и audit_client — monkeypatch'им
    `run_publisher_loop` на простую sleep-корутину.
    """
    from taskiq import TaskiqState

    from src.main import _PUBLISHER_TASK_KEY, _start_audit_outbox_publisher

    invoked = asyncio.Event()

    async def fake_loop():
        invoked.set()
        # imitate long-running loop: ждём бесконечно, пока не отменят.
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.run_publisher_loop", fake_loop
    )

    state = TaskiqState()
    try:
        await _start_audit_outbox_publisher(state)
        task = state.get(_PUBLISHER_TASK_KEY)

        assert isinstance(task, asyncio.Task)
        assert not task.done()
        # Дать event-loop'у шанс реально стартовать coroutine.
        await asyncio.wait_for(invoked.wait(), timeout=1.0)
        assert task.get_name() == "audit_outbox_publisher"
    finally:
        # cleanup, чтобы pending task не утёк в другие тесты.
        task = state.get(_PUBLISHER_TASK_KEY)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def test_publisher_shutdown_cancels_background_task(monkeypatch):
    """Shutdown-хук должен отменить task, дождаться её и удалить из state."""
    from taskiq import TaskiqState

    from src.main import (
        _PUBLISHER_TASK_KEY,
        _start_audit_outbox_publisher,
        _stop_audit_outbox_publisher,
    )

    async def fake_loop():
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.run_publisher_loop", fake_loop
    )

    state = TaskiqState()
    await _start_audit_outbox_publisher(state)
    task = state.get(_PUBLISHER_TASK_KEY)
    assert task is not None and not task.done()

    await _stop_audit_outbox_publisher(state)

    assert task.done(), "publisher task должен быть завершён после shutdown"
    assert task.cancelled(), "publisher task должен быть cancelled"
    # State очищен — повторный shutdown не должен делать ничего.
    assert state.get(_PUBLISHER_TASK_KEY) is None


async def test_publisher_shutdown_without_startup_is_noop():
    """Если по какой-то причине startup-хук не отработал (или уже cleanup
    был сделан) — shutdown не должен падать."""
    from taskiq import TaskiqState

    from src.main import _stop_audit_outbox_publisher

    state = TaskiqState()
    # Ничего не сохраняли в state — shutdown просто молча выходит.
    await _stop_audit_outbox_publisher(state)  # no exception
