"""Тесты `services/queue_loop.py` — один проход цикла за раз.

`run_polling_loop()` сам по себе бесконечный, поэтому тестируем через
`asyncio.wait_for` с коротким таймаутом: даём циклу сделать несколько
итераций и отменяем. Сеть/SSH полностью замоканы.
"""

from __future__ import annotations

import asyncio
import shlex
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncssh
import pytest

from src.core.config import get_settings
from src.services import queue_loop
from src.services.ssh_executor import ExecutionResult

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


_STARTED = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
_FINISHED = datetime(2026, 9, 9, 12, 0, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def stub_remote_writes(monkeypatch):
    """Глушим SFTP по умолчанию: `_run_one_item` кладёт `starter.sh` на стенд
    всегда, и без заглушки каждый тест этого модуля полез бы в сеть.

    Тесты, которым запись интересна сама по себе, ставят свой
    `monkeypatch.setattr` поверх — он и победит, откат идёт в обратном порядке.
    """
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(queue_loop.ssh_executor, "write_remote_file", _noop)


@pytest.fixture(autouse=True)
def stub_preflight(monkeypatch):
    """`_run_one_item` начинается с проверки внешних сервисов — в тестах она
    всегда «всё доступно».

    Без заглушки каждый тест этого модуля полез бы в реальную сеть (и, не
    дозвонившись, честно ждал бы два часа). Тесты самой проверки — в
    `test_preflight.py`; тесты, которым интересен её провал, ставят свой
    `monkeypatch.setattr` поверх.
    """
    async def _ok(**kwargs):
        return queue_loop.preflight.PreflightResult(ok=True, attempts=1)

    monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", _ok)


class TestRunOneItem:
    async def test_executes_and_reports_completed(self, monkeypatch):
        recorded = {"calls": []}

        async def fake_execute(host, username, key, command, **kwargs):
            recorded["execute_args"] = (host, username, key, command)
            recorded["on_output_chunk"] = kwargs.get("on_output_chunk")
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="all good", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_log_chunk(queue_item_id, text):
            recorded["calls"].append(("log_chunk", queue_item_id, text))

        async def fake_log_segment(queue_item_id, **fields):
            recorded["calls"].append(("log_segment", queue_item_id, fields))

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, timed_out=False):
            recorded["calls"].append(("report_completed", queue_item_id, succeeded, exit_code, error))

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_chunk", fake_log_chunk)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_1",
            "host": "10.0.0.1",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "launch_command": '--flag value', "stop_command": "STOP-TREE", "files": [],
            "launch_command_masked": "--flag '***'",
            "debug_mode": False,
            "is_retry": False,
        }

        await queue_loop._run_one_item(item)

        assert recorded["execute_args"] == ("10.0.0.1", "u", "keydata", "--flag value")

        # log-segment уходит до report_completed, ровно один раз, с
        # замаскированной командой и полным выводом от execute().
        kinds = [call[0] for call in recorded["calls"]]
        assert kinds == ["log_segment", "report_completed"]

        _, queue_item_id, fields = recorded["calls"][0]
        assert queue_item_id == "qi_1"
        assert fields["kind"] == "command"
        assert fields["status"] == "OK"
        assert fields["command_text_masked"] == shlex.join(["--flag", "***"])
        assert fields["output"] == "all good"
        assert fields["host"] == "10.0.0.1"
        assert fields["started_at"] == _STARTED
        assert fields["finished_at"] == _FINISHED

        assert recorded["calls"][1] == ("report_completed", "qi_1", True, 0, None)

        # `on_output_chunk`, переданный в execute(), реально проксирует в log_chunk.
        await recorded["on_output_chunk"]("live piece")
        assert ("log_chunk", "qi_1", "live piece") in recorded["calls"]

    async def test_uses_settings_default_timeout_when_not_overridden(self, monkeypatch):
        recorded = {}

        async def fake_execute(host, username, key, command, **kwargs):
            recorded["command_timeout"] = kwargs.get("command_timeout")
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_log_segment(*a, **k):
            pass

        async def fake_report(*a, **k):
            pass

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_2", "host": "10.0.0.1", "test_username": "u",
            "test_ssh_private_key": "keydata", "launch_command": 'x', "stop_command": "STOP-TREE", "files": [], "launch_command_masked": 'x',
            "debug_mode": False, "is_retry": False,
        }
        await queue_loop._run_one_item(item)

        from src.core.config import get_settings
        assert recorded["command_timeout"] == get_settings().ssh_command_timeout_seconds

    async def test_uses_per_test_timeout_override(self, monkeypatch):
        recorded = {}

        async def fake_execute(host, username, key, command, **kwargs):
            recorded["command_timeout"] = kwargs.get("command_timeout")
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_log_segment(*a, **k):
            pass

        async def fake_report(*a, **k):
            pass

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_3", "host": "10.0.0.1", "test_username": "u",
            "test_ssh_private_key": "keydata", "launch_command": 'x', "stop_command": "STOP-TREE", "files": [], "launch_command_masked": 'x',
            "debug_mode": False, "is_retry": False, "command_timeout_seconds": 120,
        }
        await queue_loop._run_one_item(item)

        assert recorded["command_timeout"] == 120

    async def test_reports_failure_from_execute(self, monkeypatch):
        recorded = {"calls": []}

        async def fake_execute(host, username, key, command, **kwargs):
            return ExecutionResult(
                connected=True, succeeded=False, exit_code=1, error="boom",
                output="partial output", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_log_segment(queue_item_id, **fields):
            recorded["calls"].append(("log_segment", fields))

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, timed_out=False):
            recorded["calls"].append(("report_completed", queue_item_id, succeeded, exit_code, error))

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_2",
            "host": "10.0.0.2",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "launch_command": 'cmd', "stop_command": "STOP-TREE", "files": [],
            "launch_command_masked": 'cmd',
            "debug_mode": True,
            "is_retry": True,
        }

        await queue_loop._run_one_item(item)

        assert recorded["calls"][0] == ("log_segment", {
            "kind": "command", "label": "Выполнение теста", "status": "FATAL",
            "command_text_masked": "cmd", "output": "partial output",
            "host": "10.0.0.2", "started_at": _STARTED, "finished_at": _FINISHED,
        })
        assert recorded["calls"][1] == ("report_completed", "qi_2", False, 1, "boom")

    async def test_multistep_segment_label_carries_step(self, monkeypatch):
        """сегмент лога шага подписан номером и именем шага."""
        recorded: list[dict] = []

        async def fake_execute(host, username, key, command, **kwargs):
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="ok", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_log_segment(queue_item_id, **fields):
            recorded.append(fields)

        async def fake_report(queue_item_id, **kwargs):
            return None

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        base = {
            "queue_item_id": "qi_steps", "host": "10.0.0.3", "test_username": "u",
            "test_ssh_private_key": "keydata", "launch_command": "cmd", "stop_command": "STOP-TREE",
            "files": [], "launch_command_masked": "cmd",
        }
        await queue_loop._run_one_item({**base, "step": {"index": 1, "count": 4, "name": "maxcpus=16"}})
        await queue_loop._run_one_item({**base, "step": {"index": 0, "count": 1, "name": ""}})
        assert [f["label"] for f in recorded] == [
            "Выполнение теста · шаг 2/4 «maxcpus=16»", "Выполнение теста",
        ]

    async def test_connect_failure_skips_log_segment(self, monkeypatch):
        recorded = {"calls": []}

        async def fake_execute(host, username, key, command, **kwargs):
            return ExecutionResult(
                connected=False, succeeded=False, exit_code=None,
                error="SSH authentication failed",
            )

        async def fake_log_segment(queue_item_id, **fields):
            recorded["calls"].append(("log_segment", fields))

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, timed_out=False):
            recorded["calls"].append(("report_completed", queue_item_id, succeeded, exit_code, error))

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        item = {
            "queue_item_id": "qi_3",
            "host": "10.0.0.3",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "launch_command": 'cmd', "stop_command": "STOP-TREE", "files": [],
            "launch_command_masked": 'cmd',
            "debug_mode": False,
            "is_retry": False,
        }

        await queue_loop._run_one_item(item)

        assert recorded["calls"] == [
            ("report_completed", "qi_3", False, None, "SSH authentication failed"),
        ]


class TestRunOneItemPreflight:
    """Проверка внешних сервисов стоит перед любым обращением к стенду.

    Легаси гейтило ровно так же (`backup_image.py:1045-1050`): пока
    Jira/Confluence/git/DNS недоступны, запускать тест нет смысла — `starter.sh`
    первым делом клонирует ветку с git.astralinux.ru.
    """

    def _item(self, **overrides) -> dict:
        item = {
            "queue_item_id": "qi_pf",
            "host": "10.0.0.9",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "launch_command": 'x', "stop_command": "STOP-TREE", "files": [],
            "launch_command_masked": 'x',
            "debug_mode": False,
            "is_retry": False,
        }
        item.update(overrides)
        return item

    async def test_timeout_fails_item_without_touching_the_stand(self, monkeypatch):
        recorded = {"calls": []}

        async def fake_wait(**kwargs):
            return queue_loop.preflight.PreflightResult(
                ok=False, error="external services unavailable after 40 attempts: https://git.test",
                attempts=40, unavailable=["https://git.test"],
            )

        async def fake_write_remote_file(*a, **k):
            recorded["calls"].append(("write", a[3]))

        async def fake_execute(*a, **k):
            raise AssertionError("execute must not run when preflight failed")

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, **extra):
            recorded["calls"].append(("report", queue_item_id, succeeded, error, extra))

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.ssh_executor, "write_remote_file", fake_write_remote_file)
        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        await queue_loop._run_one_item(self._item())

        # Ни SFTP, ни execute — только честный провал item'а (retry тратится,
        # но очередь стенда не встаёт навсегда).
        assert [call[0] for call in recorded["calls"]] == ["report"]
        _, queue_item_id, succeeded, error, extra = recorded["calls"][0]
        assert (queue_item_id, succeeded) == ("qi_pf", False)
        assert "https://git.test" in error
        assert extra == {}

    async def test_interrupt_during_wait_reports_interrupted(self, monkeypatch):
        recorded = {}

        async def fake_wait(**kwargs):
            return queue_loop.preflight.PreflightResult(ok=False, aborted="skip", attempts=2)

        async def fake_execute(*a, **k):
            raise AssertionError("execute must not run when the operator skipped the item")

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, **extra):
            recorded["report"] = (queue_item_id, succeeded, error, extra)

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)

        await queue_loop._run_one_item(self._item())

        assert recorded["report"] == ("qi_pf", False, None, {"interrupted": "skip"})

    async def test_wait_reports_progress_into_the_test_log(self, monkeypatch):
        """Оператор должен видеть в живом логе, чего именно ждём."""
        recorded = {"chunks": [], "interrupt_checks": 0}
        captured = {}

        async def fake_wait(*, preflight=None, on_wait=None, should_abort=None):
            captured["preflight"] = preflight
            captured["on_wait"] = on_wait
            captured["should_abort"] = should_abort
            return queue_loop.preflight.PreflightResult(ok=True, attempts=1)

        async def fake_log_chunk(queue_item_id, text):
            recorded["chunks"].append((queue_item_id, text))

        async def fake_check_interrupt(queue_item_id):
            recorded["interrupt_checks"] += 1
            return None

        async def fake_execute(*a, **k):
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.testing_client, "log_chunk", fake_log_chunk)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", noop)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", noop)
        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)

        await queue_loop._run_one_item(self._item())

        await captured["on_wait"](["https://git.test"], 360.0)
        assert recorded["chunks"][0][0] == "qi_pf"
        assert "https://git.test" in recorded["chunks"][0][1]
        assert "360" in recorded["chunks"][0][1]

        # Тот же `interrupt-check`, что и во время исполнения теста.
        assert await captured["should_abort"]() is None
        assert recorded["interrupt_checks"] == 1
        # Без `preflight` в item — env-фолбэк (`None`).
        assert captured["preflight"] is None

    async def test_item_preflight_is_passed_to_the_wait(self, monkeypatch):
        """настройки отдела из claim payload уходят в проверку как есть."""
        captured = {}
        settings = {
            "enabled": True, "http": [{"url": "https://x.test", "ok_status": "200"}],
            "dns_hosts": [], "dns_port": 53, "poll_interval_seconds": 5, "timeout_seconds": 5,
        }

        async def fake_wait(*, preflight=None, on_wait=None, should_abort=None):
            captured["preflight"] = preflight
            return queue_loop.preflight.PreflightResult(ok=False, error="down")

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", noop)

        await queue_loop._run_one_item(self._item(preflight=settings))
        assert captured["preflight"] == settings


    async def test_waiting_is_reported_for_the_ui_and_cleared(self, monkeypatch):
        """каждое ожидание — `waiting` для левой панели, в конце — `ok`."""
        states = []

        async def fake_wait(*, preflight=None, on_wait=None, should_abort=None):
            await on_wait(["https://git.test"], 180.0)
            await on_wait(["dns"], 360.0)
            return queue_loop.preflight.PreflightResult(ok=False, error="down")

        async def fake_state(queue_item_id, *, waiting, unavailable=None):
            states.append((queue_item_id, waiting, unavailable))

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.testing_client, "report_preflight_state", fake_state)
        monkeypatch.setattr(queue_loop.testing_client, "log_chunk", noop)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", noop)

        await queue_loop._run_one_item(self._item())
        assert states == [
            ("qi_pf", True, ["https://git.test"]),
            ("qi_pf", True, ["dns"]),
            ("qi_pf", False, None),
        ]

    async def test_no_wait_no_state_reports(self, monkeypatch):
        """Сервисы доступны с первой попытки — UI не дёргаем."""
        states = []

        async def fake_wait(*, preflight=None, on_wait=None, should_abort=None):
            return queue_loop.preflight.PreflightResult(ok=False, error="stop here")

        async def fake_state(*a, **k):
            states.append(a)

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(queue_loop.preflight, "wait_for_external_services", fake_wait)
        monkeypatch.setattr(queue_loop.testing_client, "report_preflight_state", fake_state)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", noop)

        await queue_loop._run_one_item(self._item())
        assert states == []

class TestRunOneItemWritesFiles:
    """файлы задания пишутся по SFTP в порядке списка, с правами,
    до запуска; провал любой записи фатален. Путей у воркера нет."""

    def _item(self, **overrides) -> dict:
        item = {
            "queue_item_id": "qi_files",
            "host": "10.0.0.5",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "files": [
                {"path": "/home/u/starter.sh", "content": "#!/bin/bash\n", "mode": "0755", "sensitive": False},
                {"path": "/home/u/git_token_qi.conf", "content": "Bearer x", "mode": "0600", "sensitive": True},
                {"path": "/home/u/dates_qi.conf", "content": "-sn 1", "mode": "0644", "sensitive": False},
                {"path": "/home/u/testenv_marker.conf", "content": "off", "mode": "0644", "sensitive": False},
            ],
            "launch_command": "sudo bash /home/u/starter.sh b git_token_qi.conf dates_qi.conf 1.8 ''",
            "launch_command_masked": "sudo bash /home/u/starter.sh b git_token_qi.conf dates_qi.conf 1.8 ''",
            "stop_command": "STOP-TREE",
            "debug_mode": False,
            "is_retry": False,
        }
        item.update(overrides)
        return item

    def _patch(self, monkeypatch, recorded, *, fail_on: str | None = None):
        async def fake_write(host, username, key, path, content, *, mode=None, connect_timeout=None):
            recorded.append(("write", path, content, mode))
            if path == fail_on:
                raise OSError("sftp broke")

        async def fake_run(host, username, key, command, **kwargs):
            recorded.append(("run", command))
            return 0

        async def fake_execute(host, username, key, command, **kwargs):
            recorded.append(("execute", command, kwargs.get("stop_command"), kwargs.get("use_pty")))
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="ok", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, timed_out=False, interrupted=None):
            recorded.append(("report", succeeded, error))

        async def fake_segment(queue_item_id, **fields):
            recorded.append(("segment", fields["command_text_masked"]))

        monkeypatch.setattr(queue_loop.ssh_executor, "write_remote_file", fake_write)
        monkeypatch.setattr(queue_loop.ssh_executor, "run_remote_command", fake_run)
        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_segment)

    async def test_writes_files_in_order_with_modes_then_executes(self, monkeypatch):
        recorded: list = []
        self._patch(monkeypatch, recorded)
        item = self._item(use_pty=True)

        await queue_loop._run_one_item(item)

        writes = [r for r in recorded if r[0] == "write"]
        assert [(w[1], w[3]) for w in writes] == [
            ("/home/u/starter.sh", "0755"), ("/home/u/git_token_qi.conf", "0600"),
            ("/home/u/dates_qi.conf", "0644"), ("/home/u/testenv_marker.conf", "0644"),
        ]
        execute = next(r for r in recorded if r[0] == "execute")
        assert recorded.index(execute) > recorded.index(writes[-1])
        assert execute[1:] == (item["launch_command"], "STOP-TREE", True)
        assert ("segment", item["launch_command_masked"]) in recorded
        assert recorded[-1] == ("report", True, None)
        # Очистки по умолчанию нет: сервер чистый.
        assert not any(r[0] == "run" for r in recorded)

    async def test_write_failure_is_fatal_and_skips_the_rest(self, monkeypatch):
        recorded: list = []
        self._patch(monkeypatch, recorded, fail_on="/home/u/git_token_qi.conf")

        await queue_loop._run_one_item(self._item())

        assert [r[1] for r in recorded if r[0] == "write"] == ["/home/u/starter.sh", "/home/u/git_token_qi.conf"]
        assert not any(r[0] == "execute" for r in recorded)
        assert recorded[-1][0] == "report" and recorded[-1][1] is False
        assert "/home/u/git_token_qi.conf" in recorded[-1][2]
        # Содержимое секретного файла в текст ошибки не попадает.
        assert "Bearer x" not in recorded[-1][2]

    async def test_cleanup_globs_run_before_writes(self, monkeypatch):
        recorded: list = []
        self._patch(monkeypatch, recorded)

        await queue_loop._run_one_item(self._item(cleanup_globs=["/home/u/testenv_*.conf"]))

        assert recorded[0] == ("run", "sudo rm -f -- /home/u/testenv_*.conf")
        assert recorded[1][0] == "write"

    async def test_unsafe_cleanup_glob_fails_item_without_running_it(self, monkeypatch):
        recorded: list = []
        self._patch(monkeypatch, recorded)

        await queue_loop._run_one_item(self._item(cleanup_globs=["/home/u/x; rm -rf /"]))

        assert not any(r[0] in ("run", "write", "execute") for r in recorded)
        assert recorded[-1][0] == "report" and "unsafe cleanup glob" in recorded[-1][2]


class TestInterruptWatcher:
    """Прерывание идущего теста: kill на стенде + `completed(interrupted=...)`."""

    def _item(self, **overrides) -> dict:
        item = {
            "queue_item_id": "qi_int",
            "host": "10.0.0.7",
            "test_username": "u",
            "test_ssh_private_key": "keydata",
            "launch_command": 'sudo bash /home/u/starter.sh', "stop_command": "STOP-TREE", "files": [],
            "launch_command_masked": 'sudo bash /home/u/starter.sh',
            "debug_mode": False,
            "is_retry": False,
        }
        item.update(overrides)
        return item

    def _patch_fast_polling(self, monkeypatch, interval: float = 0.01) -> None:
        """Опрос раз в 10мс вместо штатных семи секунд — иначе тест ждал бы их живьём."""
        real = get_settings()
        stub = SimpleNamespace(
            ssh_connect_timeout_seconds=real.ssh_connect_timeout_seconds,
            ssh_command_timeout_seconds=real.ssh_command_timeout_seconds,
            queue_poll_interval_seconds=real.queue_poll_interval_seconds,
            interrupt_poll_interval_seconds=interval,
        )
        monkeypatch.setattr(queue_loop, "get_settings", lambda: stub)

    @pytest.mark.parametrize("action", ["skip", "pause"])
    async def test_interrupt_kills_remote_process_and_reports_it(self, monkeypatch, action):
        recorded = {"calls": []}

        async def fake_execute(host, username, key, command, **kwargs):
            # Тест «висит» до тех пор, пока его не отменят снаружи.
            await asyncio.Event().wait()
            raise AssertionError("execute() must be cancelled by the watcher")

        async def fake_check_interrupt(queue_item_id):
            recorded["calls"].append(("check_interrupt", queue_item_id))
            return action

        async def fake_kill(host, username, key, **kwargs):
            recorded["calls"].append(("kill", host, username))
            return True

        async def fake_log_segment(*a, **k):
            recorded["calls"].append(("log_segment",))

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, interrupted=None, timed_out=False):
            recorded["calls"].append(("report_completed", queue_item_id, succeeded, exit_code, error, interrupted))

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.ssh_executor, "kill_remote_process", fake_kill)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)
        self._patch_fast_polling(monkeypatch)

        await asyncio.wait_for(queue_loop._run_one_item(self._item()), timeout=5)

        kinds = [call[0] for call in recorded["calls"]]
        # Прерванная сессия не оставляет ни исхода, ни полного вывода — сегмент
        # лога не заводится, только отчёт о прерывании.
        assert "log_segment" not in kinds
        assert ("kill", "10.0.0.7", "u") in recorded["calls"]
        assert recorded["calls"][-1] == (
            "report_completed", "qi_int", False, None, None, action,
        )

    async def test_natural_finish_before_interrupt_reports_normal_outcome(self, monkeypatch):
        recorded = {"calls": [], "checks": 0}

        async def fake_execute(host, username, key, command, **kwargs):
            return ExecutionResult(
                connected=True, succeeded=True, exit_code=0, error=None,
                output="done", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_check_interrupt(queue_item_id):
            recorded["checks"] += 1
            return None

        async def fake_kill(*a, **k):
            recorded["calls"].append(("kill",))
            raise AssertionError("kill must not run when the test finished on its own")

        async def fake_log_segment(queue_item_id, **fields):
            recorded["calls"].append(("log_segment", fields["status"]))

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, interrupted=None, timed_out=False):
            recorded["calls"].append(("report_completed", succeeded, exit_code, error, interrupted))

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.ssh_executor, "kill_remote_process", fake_kill)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)
        self._patch_fast_polling(monkeypatch)

        await asyncio.wait_for(queue_loop._run_one_item(self._item()), timeout=5)

        assert recorded["calls"] == [
            ("log_segment", "OK"),
            ("report_completed", True, 0, None, None),
        ]

    async def test_watcher_keeps_polling_until_interrupt_appears(self, monkeypatch):
        """Первые опросы возвращают `null` — исполнение продолжается, kill не идёт."""
        recorded = {"checks": 0, "killed": False, "interrupted": "unset"}

        async def fake_execute(host, username, key, command, **kwargs):
            await asyncio.Event().wait()

        async def fake_check_interrupt(queue_item_id):
            recorded["checks"] += 1
            return "skip" if recorded["checks"] >= 3 else None

        async def fake_kill(*a, **k):
            recorded["killed"] = True
            return True

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, interrupted=None, timed_out=False):
            recorded["interrupted"] = interrupted

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.ssh_executor, "kill_remote_process", fake_kill)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)
        self._patch_fast_polling(monkeypatch)

        await asyncio.wait_for(queue_loop._run_one_item(self._item()), timeout=5)

        assert recorded["checks"] >= 3
        assert recorded["killed"] is True
        assert recorded["interrupted"] == "skip"

    async def test_failing_interrupt_check_does_not_break_the_run(self, monkeypatch):
        """Сбой опроса — не повод обрывать уже идущий тест."""
        recorded = {}

        async def fake_execute(host, username, key, command, **kwargs):
            await _REAL_SLEEP(0.05)
            return ExecutionResult(
                connected=True, succeeded=False, exit_code=3, error="boom",
                output="out", started_at=_STARTED, finished_at=_FINISHED,
            )

        async def fake_check_interrupt(queue_item_id):
            raise RuntimeError("testing_service exploded")

        async def fake_kill(*a, **k):
            raise AssertionError("kill must not run without an interrupt action")

        async def fake_log_segment(*a, **k):
            pass

        async def fake_report(queue_item_id, *, succeeded, exit_code, error, interrupted=None, timed_out=False):
            recorded["report"] = (succeeded, exit_code, error, interrupted)

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.ssh_executor, "kill_remote_process", fake_kill)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop.testing_client, "log_segment", fake_log_segment)
        monkeypatch.setattr(queue_loop.testing_client, "report_completed", fake_report)
        self._patch_fast_polling(monkeypatch)

        await asyncio.wait_for(queue_loop._run_one_item(self._item()), timeout=5)

        assert recorded["report"] == (False, 3, "boom", None)

    async def test_external_cancellation_uses_quiet_cancel_for_both_nested_tasks(self, monkeypatch):
        """Отмена самого `_execute_with_interrupt_watch` (shutdown посреди
        теста) обязана дождаться `execute_task`/`watch_task` через
        `_quiet_cancel`, а не просто `cancel()`нуть их и сразу `raise` —
        иначе event loop может остановиться раньше, чем cleanup внутри
        `execute()` (закрытие SSH-соединения, flush аккумулятора) успеет
        докрутиться. Реальная гонка события цикла не гарантирует, что голый
        `cancel()` без `await` не сработает — раз в `_execute_with_interrupt_watch`
        уже есть `_quiet_cancel` для ровно этого случая на happy-path, эта
        ветка обязана пользоваться тем же приёмом, что и проверяет тест."""
        quiet_cancel_calls: list[asyncio.Task] = []
        original_quiet_cancel = queue_loop._quiet_cancel

        async def spy_quiet_cancel(task):
            quiet_cancel_calls.append(task)
            await original_quiet_cancel(task)

        async def fake_execute(host, username, key, command, **kwargs):
            await asyncio.Event().wait()

        async def fake_check_interrupt(queue_item_id):
            await asyncio.Event().wait()
            return None

        monkeypatch.setattr(queue_loop.ssh_executor, "execute", fake_execute)
        monkeypatch.setattr(queue_loop.testing_client, "check_interrupt", fake_check_interrupt)
        monkeypatch.setattr(queue_loop, "_quiet_cancel", spy_quiet_cancel)
        self._patch_fast_polling(monkeypatch)

        async def noop_chunk(text):
            return None

        item = self._item()
        outer_task = asyncio.ensure_future(
            queue_loop._execute_with_interrupt_watch(item, queue_loop.get_settings(), noop_chunk)
        )
        # Дать наблюдателю время дойти до первого `check_interrupt` — не
        # принципиально для самой проверки (оба пути ведут в CancelledError-
        # ветку), но так сценарий ближе к реальному «тест уже идёт».
        await _REAL_SLEEP(0.05)

        outer_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer_task

        # Обе вложенные задачи реально дождались через `_quiet_cancel` (не
        # просто получили `cancel()` без последующего `await`), и обе к этому
        # моменту действительно завершены.
        assert len(quiet_cancel_calls) == 2
        assert len(set(quiet_cancel_calls)) == 2
        assert all(task.done() for task in quiet_cancel_calls)


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
            "launch_command": 'cmd', "stop_command": "STOP-TREE", "files": [],
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

    async def test_second_item_is_claimed_before_first_one_finishes(self, monkeypatch):
        """Ядро задачи: никакого фиксированного потолка одновременных item'ов —
        второй `claim()` не ждёт, пока первый item полностью выполнится.

        `fake_claim` отдаёт ровно два item'а, а на третьем вызове зависает на
        никогда не срабатывающем `Event` — намеренно, а не `return None`:
        `None`-ветка ушла бы в `asyncio.sleep`, а зависание — единственная
        точка, где `run_polling_loop` реально уступает event loop, не завися
        от того, сколько раз event loop прокрутит `sleep(0)` за один шаг.
        """
        claimed: list[str] = []
        first_item_started = asyncio.Event()
        first_item_may_finish = asyncio.Event()

        async def fake_claim():
            if len(claimed) == 0:
                claimed.append("qi_1")
                return {"queue_item_id": "qi_1"}
            if len(claimed) == 1:
                claimed.append("qi_2")
                return {"queue_item_id": "qi_2"}
            await asyncio.Event().wait()  # больше отдавать нечего — просто виснем

        async def fake_run_one_item(item):
            if item["queue_item_id"] == "qi_1":
                first_item_started.set()
                await first_item_may_finish.wait()

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop, "_run_one_item", fake_run_one_item)

        loop_task = asyncio.ensure_future(queue_loop.run_polling_loop())
        try:
            await asyncio.wait_for(first_item_started.wait(), timeout=2.0)
            # Первый item ещё висит на своём событии — второй уже был забран.
            assert claimed == ["qi_1", "qi_2"]
        finally:
            first_item_may_finish.set()
            loop_task.cancel()
            try:
                await asyncio.wait_for(loop_task, timeout=2.0)
            except asyncio.CancelledError:
                pass

    async def test_shutdown_cancels_in_flight_items(self, monkeypatch):
        """Отмена самого `run_polling_loop` (shutdown-хук) обязана отменить и
        уже запущенные задачи item'ов, а не бросить их работать без присмотра."""
        item_task_started = asyncio.Event()
        item_task_cancelled = asyncio.Event()
        claimed = {"n": 0}

        async def fake_claim():
            if claimed["n"] == 0:
                claimed["n"] += 1
                return {"queue_item_id": "qi_hang"}
            await asyncio.Event().wait()  # больше отдавать нечего — просто виснем

        async def fake_run_one_item(item):
            item_task_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                item_task_cancelled.set()
                raise

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop, "_run_one_item", fake_run_one_item)

        loop_task = asyncio.ensure_future(queue_loop.run_polling_loop())
        await asyncio.wait_for(item_task_started.wait(), timeout=2.0)

        loop_task.cancel()
        try:
            await asyncio.wait_for(loop_task, timeout=2.0)
        except asyncio.CancelledError:
            pass

        assert item_task_cancelled.is_set()

    async def test_one_crashing_item_does_not_stop_the_loop_from_claiming_more(self, monkeypatch, caplog):
        calls = {"claim": 0, "run_one_item": []}

        async def fake_claim():
            calls["claim"] += 1
            if calls["claim"] <= 2:
                return {"queue_item_id": f"qi_{calls['claim']}"}
            return None

        async def fake_run_one_item(item):
            calls["run_one_item"].append(item["queue_item_id"])
            if item["queue_item_id"] == "qi_1":
                raise RuntimeError("item bug")

        async def fake_sleep(seconds):
            await _REAL_SLEEP(0)

        monkeypatch.setattr(queue_loop.testing_client, "claim", fake_claim)
        monkeypatch.setattr(queue_loop, "_run_one_item", fake_run_one_item)
        monkeypatch.setattr(queue_loop.asyncio, "sleep", fake_sleep)

        await _run_briefly(queue_loop.run_polling_loop())

        assert set(calls["run_one_item"]) == {"qi_1", "qi_2"}
        assert any("failed unexpectedly" in r.message for r in caplog.records)
