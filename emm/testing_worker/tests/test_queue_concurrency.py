"""Конкурентные слоты `run_polling_loop` внутри одного процесса
(`main.py::_start_queue_polling_loop`/`_stop_queue_polling_loop`).

Один процесс должен уметь обрабатывать несколько стендов одновременно без
отдельных ОС-процессов — `QUEUE_CONCURRENCY` слотов `asyncio.Task`, каждый
гоняет свою копию `run_polling_loop`. Тесты не касаются самого содержимого
`run_polling_loop` (это `test_queue_loop.py`) — только то, что слотов
запускается ровно `queue_concurrency`, что падение одного не роняет
остальные и что shutdown корректно останавливает все.
"""

from __future__ import annotations

import asyncio

import pytest
from taskiq import TaskiqState

from src import main
from src.services import queue_loop


async def _hang_forever() -> None:
    await asyncio.Event().wait()


async def _fail_immediately() -> None:
    raise RuntimeError("boom")


class TestStartQueuePollingLoop:
    async def test_creates_one_task_per_configured_slot(self, monkeypatch):
        monkeypatch.setattr(main._settings, "queue_concurrency", 3)
        monkeypatch.setattr(queue_loop, "run_polling_loop", _hang_forever)
        state = TaskiqState()

        await main._start_queue_polling_loop(state)
        try:
            tasks = state[main._QUEUE_LOOP_TASKS_KEY]
            assert len(tasks) == 3
            assert all(isinstance(t, asyncio.Task) for t in tasks)
            assert all(not t.done() for t in tasks)
            assert len({t.get_name() for t in tasks}) == 3  # уникальные имена слотов
        finally:
            await main._stop_queue_polling_loop(state)

    async def test_default_concurrency_matches_settings(self, monkeypatch):
        monkeypatch.setattr(main._settings, "queue_concurrency", 1)
        monkeypatch.setattr(queue_loop, "run_polling_loop", _hang_forever)
        state = TaskiqState()

        await main._start_queue_polling_loop(state)
        try:
            assert len(state[main._QUEUE_LOOP_TASKS_KEY]) == 1
        finally:
            await main._stop_queue_polling_loop(state)


class TestStopQueuePollingLoop:
    async def test_cancels_every_slot(self, monkeypatch):
        monkeypatch.setattr(main._settings, "queue_concurrency", 4)
        monkeypatch.setattr(queue_loop, "run_polling_loop", _hang_forever)
        state = TaskiqState()
        await main._start_queue_polling_loop(state)
        tasks = list(state[main._QUEUE_LOOP_TASKS_KEY])

        await main._stop_queue_polling_loop(state)

        assert all(t.cancelled() for t in tasks)
        assert main._QUEUE_LOOP_TASKS_KEY not in state

    async def test_noop_when_nothing_was_started(self):
        state = TaskiqState()
        await main._stop_queue_polling_loop(state)  # не должно бросать

    async def test_a_failed_slot_does_not_block_shutdown_of_the_rest(self, monkeypatch, caplog):
        """Один упавший слот — не повод не остановить остальные штатно."""
        calls = {"n": 0}

        async def _flaky() -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            await asyncio.Event().wait()

        monkeypatch.setattr(main._settings, "queue_concurrency", 3)
        monkeypatch.setattr(queue_loop, "run_polling_loop", _flaky)
        state = TaskiqState()
        await main._start_queue_polling_loop(state)
        await asyncio.sleep(0)  # дать упавшему слоту реально упасть

        await main._stop_queue_polling_loop(state)

        assert main._QUEUE_LOOP_TASKS_KEY not in state
        assert any("exited unexpectedly" in r.message for r in caplog.records)


class TestSlotIndependence:
    async def test_one_slot_crashing_does_not_cancel_siblings(self, monkeypatch, caplog):
        monkeypatch.setattr(main._settings, "queue_concurrency", 2)
        calls = {"n": 0}

        async def _slot() -> None:
            idx = calls["n"]
            calls["n"] += 1
            if idx == 0:
                raise RuntimeError("first slot dies")
            await asyncio.Event().wait()

        monkeypatch.setattr(queue_loop, "run_polling_loop", _slot)
        state = TaskiqState()
        await main._start_queue_polling_loop(state)
        tasks = list(state[main._QUEUE_LOOP_TASKS_KEY])
        await asyncio.sleep(0)

        assert tasks[0].done()
        assert not tasks[1].done()

        await main._stop_queue_polling_loop(state)
