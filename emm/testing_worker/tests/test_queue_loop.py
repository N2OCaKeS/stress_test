"""Тесты `services/queue_loop.py` — один проход цикла за раз.

`run_polling_loop()` сам по себе бесконечный, поэтому тестируем через
`asyncio.wait_for` с коротким таймаутом: даём циклу сделать несколько
итераций и отменяем. Сеть/SSH полностью замоканы.
"""

from __future__ import annotations

import asyncio

import pytest

from src.services import queue_loop

# Захвачен ДО любого monkeypatch — фейковый `sleep` ниже подменяет
# `queue_loop.asyncio.sleep` (тот же объект модуля `asyncio`, что и здесь), но
# сам обязан реально уступить event loop через настоящий `sleep(0)`, иначе
# `while True`-цикл без единой точки приостановки блокирует весь loop
# (cancel/wait_for никогда не получают шанс сработать).
_REAL_SLEEP = asyncio.sleep


async def _run_briefly(coro, timeout: float = 0.3) -> None:
    task = asyncio.ensure_future(coro)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestRunOneItem:
    async def test_executes_and_reports_completed(self, monkeypatch):
        recorded = {}

        async def fake_execute(host, username, key, command, **kwargs):
            recorded["execute_args"] = (host, username, key, command)
            return True, 0, None

        async def fake_report(queue_item_id, *, succeeded, exit_code, error):
            recorded["report"] = (queue_item_id, succeeded, exit_code, error)

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_1",
            "host": "10.0.0.1",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "command": ["--flag", "value"],
            "debug_mode": False,
            "is_retry": False,
        }

        await queue_loop._run_one_item(item)

        assert recorded["execute_args"] == ("10.0.0.1", "u", "keydata", ["--flag", "value"])
        assert recorded["report"] == ("qi_1", True, 0, None)

    async def test_reports_failure_from_execute(self, monkeypatch):
        recorded = {}

        async def fake_execute(host, username, key, command, **kwargs):
            return False, 1, "boom"

        async def fake_report(queue_item_id, *, succeeded, exit_code, error):
            recorded["report"] = (queue_item_id, succeeded, exit_code, error)

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_2",
            "host": "10.0.0.2",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "command": ["cmd"],
            "debug_mode": True,
            "is_retry": True,
        }

        await queue_loop._run_one_item(item)

        assert recorded["report"] == ("qi_2", False, 1, "boom")


class TestRunPollingLoop:
    async def test_empty_queue_sleeps_and_retries(self, monkeypatch):
        calls = {"claim": 0, "sleep": 0}

        async def fake_claim():
            calls["claim"] += 1
            return None

        async def fake_sleep(seconds):
            calls["sleep"] += 1
            await _REAL_SLEEP(0)

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop.asyncio, "sleep", fake_sleep)

        await _run_briefly(queue_loop.run_polling_loop())

        assert calls["claim"] >= 1
        assert calls["sleep"] >= 1

    async def test_item_present_executes_immediately_without_sleep(self, monkeypatch):
        calls = {"claim": 0, "run_one_item": 0, "sleep": 0}
        item = {
            "queue_item_id": "qi_x",
            "host": "10.0.0.9",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "command": ["cmd"],
            "debug_mode": False,
            "is_retry": False,
        }

        async def fake_claim():
            calls["claim"] += 1
            # Первый item есть, дальше очередь пуста — иначе loop крутится
            # вечно и `_run_briefly` не успевает отменить его чисто между
            # итерациями без sleep.
            if calls["claim"] == 1:
                return item
            return None

        async def fake_run_one_item(received_item):
            calls["run_one_item"] += 1
            assert received_item == item

        async def fake_sleep(seconds):
            calls["sleep"] += 1
            await _REAL_SLEEP(0)

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop, "_run_one_item", fake_run_one_item)
        monkeypatch.setattr(queue_loop.asyncio, "sleep", fake_sleep)

        await _run_briefly(queue_loop.run_polling_loop())

        assert calls["claim"] >= 2
        assert calls["run_one_item"] == 1

    async def test_unexpected_error_is_logged_and_loop_continues(self, monkeypatch):
        calls = {"claim": 0, "sleep": 0}

        async def fake_claim():
            calls["claim"] += 1
            if calls["claim"] == 1:
                raise RuntimeError("unexpected bug")
            return None

        async def fake_sleep(seconds):
            calls["sleep"] += 1
            await _REAL_SLEEP(0)

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop.asyncio, "sleep", fake_sleep)

        await _run_briefly(queue_loop.run_polling_loop())

        # Loop пережил RuntimeError и продолжил поллинг.
        assert calls["claim"] >= 2
        assert calls["sleep"] >= 1
