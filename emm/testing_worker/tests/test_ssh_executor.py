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


class TestExecuteRedactsSecrets:
    """`redact_secrets` — секрет не должен уйти в `error` (а значит, в
    `queue_item.error`/аудит), даже если он засветился в выводе команды."""

    async def test_secret_is_redacted_from_error_but_not_from_output(self, monkeypatch):
        _patch_import_key(monkeypatch)
        leaked = (
            '+ git -c http.extraHeader="Authorization: Bearer super-secret-token" clone ...\n'
            "boom: clone failed"
        )
        process = _FakeProcess([leaked], exit_status=1)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["do-thing"],
            redact_secrets=["Bearer super-secret-token"],
        )

        assert result.succeeded is False
        assert "Bearer super-secret-token" not in result.error
        assert "***" in result.error
        # `output` — отдельный, уже принятый канал (живой лог), не трогаем его.
        assert "Bearer super-secret-token" in result.output

    async def test_no_secrets_leaves_error_untouched(self, monkeypatch):
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["boom: something broke"], exit_status=1)
        conn = _FakeConnection(process=process)
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"], redact_secrets=None,
        )

        assert result.error == "boom: something broke"


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
        conn = _FakeRunConnection(process=process)
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
        # Обрыв SSH-канала сам процесс на стенде не гасит — таймаут обязан
        # явно его убить, симметрично ручному interrupt-пути.
        assert conn.ran == [("sudo pkill -f starter.sh", False)]

    async def test_kill_failure_on_timeout_does_not_hide_the_timeout_result(self, monkeypatch):
        """`kill_remote_process` best-effort — его собственный провал не должен
        помешать вернуть честный таймаут-исход вызывающему."""
        _patch_import_key(monkeypatch)
        process = _FakeProcess(["partial output"], hang_after=True)
        conn = _FakeRunConnection(process=process, run_exc=asyncssh.ChannelOpenError(1, "no channel"))
        _patch_connect(monkeypatch, conn=conn)

        result = await ssh_executor.execute(
            "10.0.0.1", "u", "keydata", ["cmd"], command_timeout=0.05,
        )

        assert result.succeeded is False
        assert result.error is not None and "command timed out" in result.error

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


class _FakeRunConnection(_FakeConnection):
    """`_FakeConnection` + `run()` для `kill_remote_process`."""

    def __init__(
        self, run_exc: Exception | None = None, exit_status: int = 0, process: _FakeProcess | None = None,
    ) -> None:
        super().__init__(process=process)
        self._run_exc = run_exc
        self._exit_status = exit_status
        self.ran: list[tuple[str, bool]] = []

    async def run(self, command: str, *, check: bool = True):
        self.ran.append((command, check))
        if self._run_exc is not None:
            raise self._run_exc
        return SimpleNamespace(exit_status=self._exit_status)


class TestKillRemoteProcess:
    async def test_sends_pkill_over_a_separate_connection(self, monkeypatch):
        _patch_import_key(monkeypatch)
        conn = _FakeRunConnection()
        _patch_connect(monkeypatch, conn=conn)

        killed = await ssh_executor.kill_remote_process("10.0.0.1", "u", "keydata")

        assert killed is True
        assert conn.ran == [("sudo pkill -f starter.sh", False)]

    async def test_nonzero_pkill_exit_is_not_a_failure(self, monkeypatch):
        """`pkill` возвращает 1, когда гасить уже нечего — тест успел упасть сам."""
        _patch_import_key(monkeypatch)
        conn = _FakeRunConnection(exit_status=1)
        _patch_connect(monkeypatch, conn=conn)

        assert await ssh_executor.kill_remote_process("10.0.0.1", "u", "keydata") is True

    async def test_invalid_private_key_is_best_effort(self, monkeypatch):
        _patch_import_key(monkeypatch, import_exc=asyncssh.KeyImportError("bad key"))

        assert await ssh_executor.kill_remote_process("10.0.0.1", "u", "not-a-key") is False

    @pytest.mark.parametrize(
        "connect_exc",
        [
            asyncssh.PermissionDenied("denied"),
            asyncssh.ConnectionLost("lost"),
            OSError("no route to host"),
            TimeoutError("timed out"),
        ],
    )
    async def test_connect_failure_is_swallowed(self, monkeypatch, connect_exc):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=connect_exc)

        assert await ssh_executor.kill_remote_process("10.0.0.1", "u", "keydata") is False

    async def test_run_failure_is_swallowed(self, monkeypatch):
        _patch_import_key(monkeypatch)
        conn = _FakeRunConnection(run_exc=asyncssh.ChannelOpenError(1, "no channel"))
        _patch_connect(monkeypatch, conn=conn)

        assert await ssh_executor.kill_remote_process("10.0.0.1", "u", "keydata") is False


class _FakeSftpFile:
    """Минимальный `SFTPClientFile` — только `write()` под `async with`."""

    def __init__(self, sink: list[str], write_exc: Exception | None = None) -> None:
        self._sink = sink
        self._write_exc = write_exc

    async def write(self, content: str) -> None:
        if self._write_exc is not None:
            raise self._write_exc
        self._sink.append(content)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSftpClient:
    """Минимальный `SFTPClient` — `open()` отдаёт `_FakeSftpFile` под `async with`."""

    def __init__(
        self, sink: list[str], *, open_exc: Exception | None = None, write_exc: Exception | None = None,
    ) -> None:
        self._sink = sink
        self._open_exc = open_exc
        self._write_exc = write_exc
        self.opened: list[tuple[str, str]] = []

    def open(self, path: str, mode: str):
        self.opened.append((path, mode))
        if self._open_exc is not None:
            raise self._open_exc
        return _FakeSftpFile(self._sink, write_exc=self._write_exc)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSftpConnection(_FakeConnection):
    """`_FakeConnection` + `start_sftp_client()` для `write_remote_file`."""

    def __init__(self, sftp: _FakeSftpClient | None = None, sftp_exc: Exception | None = None) -> None:
        super().__init__()
        self._sftp = sftp
        self._sftp_exc = sftp_exc

    def start_sftp_client(self):
        if self._sftp_exc is not None:
            raise self._sftp_exc
        return self._sftp


class TestWriteRemoteFile:
    async def test_writes_content_via_sftp(self, monkeypatch):
        _patch_import_key(monkeypatch)
        sink: list[str] = []
        sftp = _FakeSftpClient(sink)
        conn = _FakeSftpConnection(sftp=sftp)
        _patch_connect(monkeypatch, conn=conn)

        await ssh_executor.write_remote_file(
            "10.0.0.1", "u", "keydata", "/home/u/dates_qi_1.conf", "-sn 1 -tcv 1.8.5",
        )

        assert sink == ["-sn 1 -tcv 1.8.5"]
        assert sftp.opened == [("/home/u/dates_qi_1.conf", "w")]

    async def test_invalid_private_key_raises(self, monkeypatch):
        _patch_import_key(monkeypatch, import_exc=asyncssh.KeyImportError("bad key"))

        with pytest.raises(ValueError, match="invalid SSH private key"):
            await ssh_executor.write_remote_file(
                "10.0.0.1", "u", "not-a-key", "/home/u/dates.conf", "content",
            )

    async def test_connect_failure_propagates(self, monkeypatch):
        _patch_import_key(monkeypatch)
        _patch_connect(monkeypatch, connect_exc=asyncssh.PermissionDenied("denied"))

        with pytest.raises(asyncssh.PermissionDenied):
            await ssh_executor.write_remote_file(
                "10.0.0.1", "u", "keydata", "/home/u/dates.conf", "content",
            )

    async def test_sftp_write_failure_propagates(self, monkeypatch):
        _patch_import_key(monkeypatch)
        sftp = _FakeSftpClient([], write_exc=asyncssh.SFTPError(asyncssh.FX_FAILURE, "disk full"))
        conn = _FakeSftpConnection(sftp=sftp)
        _patch_connect(monkeypatch, conn=conn)

        with pytest.raises(asyncssh.SFTPError):
            await ssh_executor.write_remote_file(
                "10.0.0.1", "u", "keydata", "/home/u/dates.conf", "content",
            )


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
