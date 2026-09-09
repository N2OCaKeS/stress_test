"""Тесты `services/ssh_executor.py` — без реального SSH-сервера.

`asyncssh.connect`/`asyncssh.import_private_key` подменяются моками на
уровне модуля. Плюс отдельная группа тестов на `shlex.join` — главный
инвариант: резолвленный `command` (список аргументов от `testing_service`)
не должен разваливаться на отдельные shell-команды через пробелы/`;`/`&&`.
"""

from __future__ import annotations

import shlex

import asyncssh
import pytest

from src.services import ssh_executor


class _FakeResult:
    def __init__(self, exit_status: int, stdout: str = "", stderr: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeConnection:
    """Минимальный async-context-manager, имитирующий SSHClientConnection."""

    def __init__(self, run_result=None, run_exc: Exception | None = None) -> None:
        self._run_result = run_result
        self._run_exc = run_exc
        self.ran_commands: list[str] = []

    async def run(self, cmd: str, *, check: bool, timeout: float):
        self.ran_commands.append(cmd)
        if self._run_exc is not None:
            raise self._run_exc
        return self._run_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_connect(monkeypatch, conn=None, connect_exc: Exception | None = None):
    async def _fake_connect(**kwargs):
        if connect_exc is not None:
            raise connect_exc
        return conn

    monkeypatch.setattr(asyncssh, "connect", _fake_connect)


def _patch_import_key(monkeypatch, key=object(), import_exc: Exception | None = None):
    def _fake_import(material):
        if import_exc is not None:
            raise import_exc
        return key

    monkeypatch.setattr(asyncssh, "import_private_key", _fake_import)


class TestExecuteSuccess:
    async def test_exit_status_zero_is_success(self, monkeypatch):
        _patch_import_key(monkeypatch)
        conn = _FakeConnection(run_result=_FakeResult(0, stdout="ok"))
        _patch_connect(monkeypatch, conn=conn)

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
            ["--flag", "value"],
        )

        assert succeeded is True
        assert exit_code == 0
        assert error is None
        assert conn.ran_commands == [shlex.join(["--flag", "value"])]


class TestExecuteCommandFailure:
    async def test_nonzero_exit_status_reports_stderr_tail(self, monkeypatch):
        _patch_import_key(monkeypatch)
        conn = _FakeConnection(run_result=_FakeResult(1, stdout="", stderr="boom: something broke"))
        _patch_connect(monkeypatch, conn=conn)

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["do-thing"],
        )

        assert succeeded is False
        assert exit_code == 1
        assert error == "boom: something broke"

    async def test_long_stderr_is_truncated_to_tail(self, monkeypatch):
        _patch_import_key(monkeypatch)
        long_stderr = "x" * 5000 + "REAL_CAUSE_AT_END"
        conn = _FakeConnection(run_result=_FakeResult(1, stderr=long_stderr))
        _patch_connect(monkeypatch, conn=conn)

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["do-thing"],
        )

        assert succeeded is False
        assert error is not None
        assert error.endswith("REAL_CAUSE_AT_END")
        assert len(error) <= 1800


class TestExecuteConnectFailure:
    async def test_invalid_private_key(self, monkeypatch):
        _patch_import_key(monkeypatch, import_exc=asyncssh.KeyImportError("bad key"))

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "not-a-key", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None and "invalid SSH private key" in error

    async def test_auth_failure(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.PermissionDenied("denied"))

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None and "authentication failed" in error

    async def test_connect_timeout(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=TimeoutError("timed out"))

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None and "timed out" in error

    async def test_generic_asyncssh_error(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.ConnectionLost("lost"))

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None

    async def test_os_error(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=OSError("no route to host"))

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None and "connection failed" in error

    async def test_run_timeout_after_successful_connect(self, monkeypatch):
        _patch_import_key(monkeypatch)
        # `asyncssh.TimeoutError` (a `ProcessError` subclass) requires a full
        # process-result payload to construct; the plain builtin `TimeoutError`
        # it inherits from is what our except-clause actually matches on, and
        # is enough to exercise the same code path.
        conn = _FakeConnection(run_exc=TimeoutError("command took too long"))
        _patch_connect(monkeypatch, conn=conn)

        succeeded, exit_code, error = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
        )

        assert succeeded is False
        assert exit_code is None
        assert error is not None and "command timed out" in error


class TestShlexJoinSafety:
    """Резолвленные аргументы не должны разваливаться в отдельные shell-команды."""

    @pytest.mark.parametrize(
        "args",
        [
            ["--flag1", "value with spaces"],
            ["--flag2", "value; rm -rf /"],
            ["--flag3", "a && b || c"],
            ["--flag4", "$(whoami)"],
            ["--flag5", "back`tick`"],
            ["single-arg-no-flags"],
            [],
        ],
    )
    def test_roundtrip_via_shlex_split(self, args):
        joined = shlex.join(args)
        assert shlex.split(joined) == args
