"""Тесты `services/ssh_executor.py` — без реального SSH-сервера.

`asyncssh.connect`/`asyncssh.import_private_key` подменяются моками на
уровне модуля, `conn.create_process()` возвращает фейковый процесс с
async-читаемым `stdout` (merged stdout+stderr, как и просит реальный код).
Плюс отдельная группа тестов на `shlex.join` — главный инвариант:
резолвленный `command` (список аргументов от `testing_service`) не должен
разваливаться на отдельные shell-команды через пробелы/`;`/`&&`.
"""

from __future__ import annotations

import asyncio
import shlex
from types import SimpleNamespace

import asyncssh
import pytest

from src.services import ssh_executor


class _FakeStdout:
    """Отдаёт заранее заданные куски по одному на каждый `read()`, потом EOF.

    `hang_after=True` — вместо EOF после исчерпания кусков `read()` висит
    вечно (имитация зависшего на удалённой стороне процесса), полагается на
    внешний таймаут (`asyncio.wait_for`), который его оборвёт.
    """

    def __init__(self, chunks: list[str], delay: float = 0.0, hang_after: bool = False) -> None:
        self._chunks = list(chunks)
        self._delay = delay
        self._hang_after = hang_after

    async def read(self, n: int) -> str:
        if self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            return self._chunks.pop(0)
        if self._hang_after:
            await asyncio.Event().wait()
        return ""


class _FakeProcess:
    def __init__(
        self, chunks: list[str], exit_status: int = 0, *,
        chunk_delay: float = 0.0, hang_after: bool = False, wait_exc: Exception | None = None,
    ) -> None:
        self.stdout = _FakeStdout(chunks, delay=chunk_delay, hang_after=hang_after)
        self._exit_status = exit_status
        self._wait_exc = wait_exc

    async def wait(self):
        if self._wait_exc is not None:
            raise self._wait_exc
        return SimpleNamespace(exit_status=self._exit_status)


class _FakeConnection:
    """Минимальный async-context-manager, имитирующий SSHClientConnection."""

    def __init__(self, process: _FakeProcess | None = None, create_process_exc: Exception | None = None) -> None:
        self._process = process
        self._create_process_exc = create_process_exc
        self.created_commands: list[tuple[str, object, object]] = []

    async def create_process(self, cmd: str, *, stdin, stderr):
        self.created_commands.append((cmd, stdin, stderr))
        if self._create_process_exc is not None:
            raise self._create_process_exc
        return self._process

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
        process = _FakeProcess(["ok"], exit_status=0)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
            ["--flag", "value"],
        )

        assert result.connected is True
        assert result.succeeded is True
        assert result.exit_code == 0
        assert result.error is None
        assert result.output == "ok"
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.finished_at >= result.started_at
        cmd, stdin, stderr = conn.created_commands[0]
        assert cmd == shlex.join(["--flag", "value"])
        assert stdin == asyncssh.DEVNULL
        assert stderr == asyncssh.STDOUT

    async def test_output_accumulates_across_multiple_reads(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["part1 ", "part2 ", "part3"], exit_status=0)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.output == "part1 part2 part3"


class TestExecuteCommandFailure:
    async def test_nonzero_exit_status_reports_output_tail(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["boom: something broke"], exit_status=1)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["do-thing"])

        assert result.connected is True
        assert result.succeeded is False
        assert result.exit_code == 1
        assert result.error == "boom: something broke"
        assert result.output == "boom: something broke"

    async def test_long_output_error_is_truncated_to_tail(self, monkeypatch):
        _patch_import_key(monkeypatch)
        long_output = "x" * 5000 + "REAL_CAUSE_AT_END"
        process = _FakeProcess([long_output], exit_status=1)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["do-thing"])

        assert result.succeeded is False
        assert result.error is not None
        assert result.error.endswith("REAL_CAUSE_AT_END")
        assert len(result.error) <= 1800
        assert result.output == long_output


class TestExecuteConnectFailure:
    async def test_invalid_private_key(self, monkeypatch):
        _patch_import_key(monkeypatch, import_exc=asyncssh.KeyImportError("bad key"))

        result = await ssh_executor.execute("10.0.0.1", "u", "not-a-key", ["cmd"])

        assert result.connected is False
        assert result.succeeded is False
        assert result.exit_code is None
        assert result.error is not None and "invalid SSH private key" in result.error
        assert result.output == ""

    async def test_auth_failure(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.PermissionDenied("denied"))

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is False
        assert result.exit_code is None
        assert result.error is not None and "authentication failed" in result.error

    async def test_connect_timeout(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=TimeoutError("timed out"))

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is False
        assert result.exit_code is None
        assert result.error is not None and "timed out" in result.error

    async def test_generic_asyncssh_error(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.ConnectionLost("lost"))

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is False
        assert result.exit_code is None
        assert result.error is not None

    async def test_os_error(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=OSError("no route to host"))

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is False
        assert result.exit_code is None
        assert result.error is not None and "connection failed" in result.error

    async def test_create_process_failure(self, monkeypatch):
        _patch_import_key(monkeypatch)
        conn = _FakeConnection(create_process_exc=asyncssh.ChannelOpenError(1, "no channel"))
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is False
        assert result.exit_code is None
        assert result.error is not None and "process start error" in result.error


class TestExecuteRunFailureAfterConnect:
    async def test_run_timeout_after_successful_connect(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["partial output"], hang_after=True)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"], command_timeout=0.05,
        )

        assert result.connected is True
        assert result.succeeded is False
        assert result.exit_code is None
        assert result.error is not None and "command timed out" in result.error
        # То, что успело прийти до таймаута, не теряется.
        assert result.output == "partial output"

    async def test_wait_error_after_streaming(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["some output"], wait_exc=asyncssh.ConnectionLost("dropped"))
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.connected is True
        assert result.succeeded is False
        assert result.exit_code is None
        assert result.error is not None
        assert result.output == "some output"


class TestExecuteChunkStreaming:
    async def test_on_output_chunk_receives_accumulated_text(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["hello "], exit_status=0)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        received: list[str] = []

        async def _on_chunk(text: str) -> None:
            received.append(text)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"], on_output_chunk=_on_chunk,
        )

        assert result.output == "hello "
        assert "".join(received) == "hello "

    async def test_chunk_flushed_immediately_once_max_bytes_reached(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["x" * 10, "y" * 10], exit_status=0)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        received: list[str] = []

        async def _on_chunk(text: str) -> None:
            received.append(text)

        await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
            on_output_chunk=_on_chunk, chunk_max_bytes=5,
        )

        # Порог (5 байт) меньше первого же куска (10 байт) — он уходит сразу,
        # не дожидаясь конца исполнения; второй кусок уходит финальным flush'ем.
        assert len(received) == 2
        assert received[0] == "x" * 10
        assert received[1] == "y" * 10

    async def test_no_callback_means_no_crash_and_output_still_collected(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["abc"], exit_status=0)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"])

        assert result.output == "abc"

    async def test_no_chunk_sent_on_connect_failure(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.PermissionDenied("denied"))

        received: list[str] = []

        async def _on_chunk(text: str) -> None:
            received.append(text)

        await ssh_executor.execute("10.0.0.1", "u", "keydata", ["cmd"], on_output_chunk=_on_chunk)

        assert received == []

    async def test_ticker_flushes_slow_trickling_output(self, monkeypatch):
        _patch_import_key(monkeypatch)
        # Каждый кусок приходит с небольшой задержкой — дольше, чем
        # `chunk_interval_seconds` ниже — так тикер обязан сработать хотя бы
        # раз до того, как поток вообще закроется.
        process = _FakeProcess(["a", "b", "c"], exit_status=0, chunk_delay=0.05)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        received: list[str] = []

        async def _on_chunk(text: str) -> None:
            received.append(text)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"],
            on_output_chunk=_on_chunk, chunk_interval_seconds=0.02, chunk_max_bytes=10_000,
        )

        assert result.output == "abc"
        assert "".join(received) == "abc"
        # Тикер успел сработать хотя бы раз посреди потока, не только в
        # финальном flush'е — иначе живой лог был бы виден только постфактум.
        assert len(received) >= 2


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
