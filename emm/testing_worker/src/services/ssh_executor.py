"""Исполнение резолвленной команды теста на стенде по SSH.

`launch_command` от `testing_service` — уже готовая shell-строка
(аргументы экранированы `shlex.join` на стороне сервиса). Исполнение —
потоковое, через `conn.create_process()`, не блокирующий `conn.run()`.

С pty вывод построчный (без него `python` буферит блоками по 4-8 КБ, и лог
приходит пачкой в конце). `_StreamFilter` приводит `\r\n` к `\n`, вырезает
ANSI-коды и `redact_values` задания из каждого куска, включая значения на
границе кусков. Вывод копится целиком в памяти (нужен для итогового
`log-segment`) и одновременно отдаётся кусками через `on_output_chunk`, не
чаще `chunk_interval_seconds` и не крупнее `chunk_max_bytes`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncssh

logger = logging.getLogger("testing_worker.ssh_executor")

# Сколько последних символов вывода класть в error при провале команды.
# Лимит поля `error` на стороне testing_service — 2048 символов (весь JSON,
# не только это поле), оставляем запас под остальные ключи тела.
_ERROR_TAIL_MAX_LEN = 1800

# Размер одного read() с удалённого канала — гранулярность, которой ждём
# данные от asyncssh. Не путать с `chunk_max_bytes` ниже: это про то, как
# часто мы вообще просыпаемся, а не про размер отправляемого наружу куска.
_READ_SIZE = 8192

DEFAULT_CHUNK_MAX_BYTES = 4096
DEFAULT_CHUNK_INTERVAL_SECONDS = 2.5

OnOutputChunk = Callable[[str], Awaitable[None]]


def _tail(text: str, max_len: int) -> str:
    """Хвост строки длиной не больше `max_len` — самая свежая часть вывода обычно
    несёт причину провала (traceback/assertion в конце)."""
    if len(text) <= max_len:
        return text
    return text[-max_len:]


def _redact(text: str, secrets: list[str] | None) -> str:
    """Вырезать из `text` известные секреты перед тем, как класть его в `error`.

    Не трогает сам `output` (тот идёт в `log-chunk`/`log-segment` как есть —
    это отдельный, уже принятый риск), только сводку, которая уезжает в
    `queue_item.error` и оттуда в аудит-событие. Если тест падает сразу
    после клонирования, хвост вывода ещё несёт трассировку `set -x` с
    токеном в открытом виде — этот путь не должен его туда протаскивать
    второй раз.
    """
    if not secrets:
        return text
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


@dataclass
class ExecutionResult:
    """Итог одной попытки исполнения — от коннекта до завершения процесса.

    `connected=False` — до запуска команды дело не дошло (SSH-уровня провал),
    только `completed(succeeded=False)`, без `log-segment`. `connected=True` —
    команда стартовала, `output` несёт всё, что накопилось, даже при провале.
    `timed_out=True` — упёрлись в `command_timeout`, не в код возврата;
    прокидывается в `report_completed`, чтобы item завёлся как `timed_out`.
    """

    connected: bool
    succeeded: bool
    exit_code: int | None
    error: str | None
    output: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    timed_out: bool = False


# ANSI: CSI (`ESC [ … финальный байт`), OSC (`ESC ] … BEL|ESC \\`) и
# двухсимвольные `ESC <символ>`.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# Незаконченная ANSI-последовательность в хвосте куска длиннее этого — не
# последовательность, отдаём как есть.
_ANSI_HOLD_MAX = 64


class _StreamFilter:
    """Нормализует поток вывода перед логом: `\r\n` → `\n`, ANSI вырезан
    (только при pty), секреты → `***`.

    `feed()` возвращает то, что уже можно отдавать; хвост, который может
    оказаться началом секрета, `\r` перед возможным `\n` или начало
    ANSI-последовательности придерживается до следующего `feed()`/`finish()`.
    """

    def __init__(self, secrets: list[str] | None, *, terminal: bool) -> None:
        self._secrets = sorted({s for s in (secrets or []) if s}, key=len, reverse=True)
        self._terminal = terminal
        self._held = ""

    def _clean(self, text: str) -> str:
        if self._terminal:
            text = _ANSI_RE.sub("", text.replace("\r\n", "\n"))
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _hold_len(self, text: str) -> int:
        hold = 0
        for secret in self._secrets:
            for n in range(min(len(secret) - 1, len(text)), 0, -1):
                if text.endswith(secret[:n]):
                    hold = max(hold, n)
                    break
        if self._terminal:
            if text.endswith("\r"):
                hold = max(hold, 1)
            esc = text.rfind("\x1b")
            if esc != -1 and len(text) - esc <= _ANSI_HOLD_MAX and not _ANSI_RE.match(text, esc):
                hold = max(hold, len(text) - esc)
        return hold

    def feed(self, chunk: str) -> str:
        text = self._clean(self._held + chunk)
        hold = self._hold_len(text)
        self._held = text[len(text) - hold:] if hold else ""
        return text[:len(text) - hold] if hold else text

    def finish(self) -> str:
        text, self._held = self._held, ""
        if self._terminal:
            text = text.replace("\r", "")
        return text


class _ChunkAccumulator:
    """Копит полный вывод и параллельно отдаёт наружу нарастающие куски.

    Наружу (`on_chunk`) уходит только то, что накопилось с прошлого
    `flush()` — либо когда пришедший кусок сам разово переваливает за
    `max_bytes` (пуш из `add()`), либо когда `flush()` вызывает тикер
    снаружи по таймеру. Полный текст остаётся в памяти отдельно — он нужен
    целиком для финального `log-segment`, log-chunk'и его не заменяют.
    """

    def __init__(self, on_chunk: OnOutputChunk | None, max_bytes: int) -> None:
        self._on_chunk = on_chunk
        self._max_bytes = max_bytes
        self._parts: list[str] = []
        self._pending: list[str] = []
        self._pending_len = 0
        self._lock = asyncio.Lock()

    @property
    def full_output(self) -> str:
        return "".join(self._parts)

    async def add(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        if self._on_chunk is None:
            return
        async with self._lock:
            self._pending.append(text)
            self._pending_len += len(text)
            if self._pending_len >= self._max_bytes:
                await self._flush_locked()

    async def flush(self) -> None:
        if self._on_chunk is None:
            return
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._pending:
            return
        text = "".join(self._pending)
        self._pending = []
        self._pending_len = 0
        await self._on_chunk(text)


async def _ticker(accumulator: _ChunkAccumulator, interval: float, stop: asyncio.Event) -> None:
    """Форсит `flush()` раз в `interval` секунд, пока не выставлен `stop`."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            await accumulator.flush()


async def _stream_process_output(process, accumulator: _ChunkAccumulator, stream_filter: _StreamFilter) -> None:
    """Читает merged stdout+stderr до EOF, кусками, через фильтр — в аккумулятор."""
    while True:
        chunk = await process.stdout.read(_READ_SIZE)
        if chunk == "":
            break
        await accumulator.add(stream_filter.feed(chunk))


async def execute(
    host: str,
    test_username: str,
    test_ssh_private_key: str,
    command: str,
    *,
    stop_command: str,
    use_pty: bool = True,
    connect_timeout: float = 30.0,
    command_timeout: float = 3600.0,
    on_output_chunk: OnOutputChunk | None = None,
    chunk_max_bytes: int = DEFAULT_CHUNK_MAX_BYTES,
    chunk_interval_seconds: float = DEFAULT_CHUNK_INTERVAL_SECONDS,
    redact_secrets: list[str] | None = None,
) -> ExecutionResult:
    """Подключиться к `host` по ключу и потоково исполнить `command`.

    Никакого fallback на `test_password` при провале ключа — ключ приходит
    заполненным всегда, провал ключа фиксируется как обычный провал.
    `stop_command` шлём отдельным коннектом при `command_timeout`.
    `redact_secrets` вырезаются из живого лога, `output` и `error`.
    """
    try:
        client_key = asyncssh.import_private_key(test_ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        logger.warning("ssh_executor: invalid private key for host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"invalid SSH private key: {type(exc).__name__}",
        )

    cmd_str = command

    try:
        conn = await asyncssh.connect(
            host=host,
            username=test_username,
            client_keys=[client_key],
            # Стенды часто переустанавливаются, host-key меняется при каждом
            # reimage — TOFU/known_hosts тут непрактичен, тот же принятый в
            # монорепо подход, что и у server_worker'а к тестовым серверам.
            known_hosts=None,
            connect_timeout=connect_timeout,
            login_timeout=connect_timeout,
        )
    except asyncssh.PermissionDenied as exc:
        logger.warning("ssh_executor: auth failed for host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"SSH authentication failed: {type(exc).__name__}",
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor: connect timed out for host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"SSH connect timed out: {type(exc).__name__}",
        )
    except asyncssh.Error as exc:
        logger.warning("ssh_executor: asyncssh error connecting to host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"SSH connect error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN],
        )
    except OSError as exc:
        logger.warning("ssh_executor: connect failed for host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"connection failed: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN],
        )

    accumulator = _ChunkAccumulator(on_output_chunk, chunk_max_bytes)
    stream_filter = _StreamFilter(redact_secrets, terminal=use_pty)

    async with conn:
        try:
            if use_pty:
                # С терминалом stdout/stderr сведены удалённой стороной; stdin
                # не закрываем — EOF в pty прочитался бы программой как ^D.
                process = await conn.create_process(cmd_str, term_type="xterm", term_size=(200, 50))
            else:
                process = await conn.create_process(cmd_str, stdin=asyncssh.DEVNULL, stderr=asyncssh.STDOUT)
        except asyncssh.Error as exc:
            logger.warning("ssh_executor: failed to start process on host=%s: %s", host, type(exc).__name__)
            return ExecutionResult(
                connected=False, succeeded=False, exit_code=None,
                error=f"SSH process start error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN],
            )

        started_at = datetime.now(timezone.utc)
        stop_ticker = asyncio.Event()
        ticker_task = asyncio.ensure_future(_ticker(accumulator, chunk_interval_seconds, stop_ticker))
        exit_status: int | None = None
        run_error: str | None = None
        command_timed_out = False
        try:
            await asyncio.wait_for(
                _stream_process_output(process, accumulator, stream_filter), timeout=command_timeout,
            )
            result = await process.wait()
            exit_status = result.exit_status
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("ssh_executor: command timed out for host=%s: %s", host, type(exc).__name__)
            run_error = f"SSH command timed out: {type(exc).__name__}"
            command_timed_out = True
            # Обрыв этого SSH-канала сам по себе `sudo bash starter.sh` на
            # стенде не гасит (см. module docstring про interrupt-путь) —
            # без явного kill процесс просто продолжит жить после того, как
            # мы уже отчитались провалом по таймауту.
            await kill_remote_process(
                host, test_username, test_ssh_private_key,
                stop_command=stop_command, connect_timeout=connect_timeout,
            )
        except asyncssh.Error as exc:
            logger.warning("ssh_executor: asyncssh error running command on host=%s: %s", host, type(exc).__name__)
            run_error = f"SSH run error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN]
        finally:
            await accumulator.add(stream_filter.finish())
            stop_ticker.set()
            await ticker_task
            await accumulator.flush()

    finished_at = datetime.now(timezone.utc)
    output = accumulator.full_output

    if run_error is not None:
        return ExecutionResult(
            connected=True, succeeded=False, exit_code=None, error=run_error,
            output=output, started_at=started_at, finished_at=finished_at,
            timed_out=command_timed_out,
        )

    if exit_status == 0:
        return ExecutionResult(
            connected=True, succeeded=True, exit_code=0, error=None,
            output=output, started_at=started_at, finished_at=finished_at,
        )

    error = _redact(_tail(output.strip(), _ERROR_TAIL_MAX_LEN), redact_secrets)
    error = error or f"command exited with status {exit_status}"
    return ExecutionResult(
        connected=True, succeeded=False, exit_code=exit_status, error=error,
        output=output, started_at=started_at, finished_at=finished_at,
    )


async def kill_remote_process(
    host: str,
    test_username: str,
    test_ssh_private_key: str,
    *,
    stop_command: str,
    connect_timeout: float = 30.0,
    run_timeout: float = 120.0,
) -> bool:
    """Остановить идущий на стенде тест командой остановки профиля.

    Команда (собрана testing_service) находит `starter.sh` по пути и
    убивает всё дерево его потомков по PPID: TERM, пауза, KILL — под
    `sudo`. Легаси `pkill -f starter.sh` оставлял жить `run.py`.

    Отдельное соединение, а не тот же канал, на котором висит `execute()` —
    тот занят чтением вывода до EOF и послать по нему ещё одну команду нельзя.

    Best-effort: любая SSH-ошибка (стенд не отвечает, ключ протух, сеть легла)
    не поднимается наружу — возвращается `False` и пишется WARNING. `True` —
    команда отработала (её код возврата не важен).
    """
    try:
        client_key = asyncssh.import_private_key(test_ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        logger.warning("ssh_executor.kill: invalid private key for host=%s: %s", host, type(exc).__name__)
        return False

    try:
        conn = await asyncssh.connect(
            host=host,
            username=test_username,
            client_keys=[client_key],
            # Тот же принцип, что у execute() — стенды часто переустанавливаются.
            known_hosts=None,
            connect_timeout=connect_timeout,
            login_timeout=connect_timeout,
        )
    except (asyncssh.Error, OSError, asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor.kill: cannot connect to host=%s: %s", host, type(exc).__name__)
        return False

    try:
        async with conn:
            await asyncio.wait_for(conn.run(stop_command, check=False), timeout=run_timeout)
    except (asyncssh.Error, OSError, asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor.kill: stop command failed on host=%s: %s", host, type(exc).__name__)
        return False

    logger.info("ssh_executor.kill: stop command sent to host=%s", host)
    return True


async def run_remote_command(
    host: str,
    username: str,
    ssh_private_key: str,
    command: str,
    *,
    connect_timeout: float = 30.0,
    run_timeout: float = 120.0,
) -> int | None:
    """Короткая служебная команда на стенде (очистка файлов перед записью).

    Исключения SSH не перехватываются — как у `write_remote_file`.
    Возвращает код выхода.
    """
    try:
        client_key = asyncssh.import_private_key(ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid SSH private key: {type(exc).__name__}") from exc
    conn = await asyncssh.connect(
        host=host, username=username, client_keys=[client_key], known_hosts=None,
        connect_timeout=connect_timeout, login_timeout=connect_timeout,
    )
    async with conn:
        result = await asyncio.wait_for(conn.run(command, check=False), timeout=run_timeout)
    return result.exit_status


async def write_remote_file(
    host: str,
    username: str,
    ssh_private_key: str,
    remote_path: str,
    content: str,
    *,
    mode: str | None = None,
    connect_timeout: float = 30.0,
) -> None:
    """Кладёт `content` в `remote_path` на стенде по SFTP, отдельным короткоживущим коннектом.

    Нужен перед `execute()` для новой архитектуры запуска теста: `starter.sh`
    читает `dates.conf` с диска, значит файл должен там оказаться раньше
    команды его запуска. Отдельная функция, а не параметр `execute()` —
    запись файла и запуск команды разные шаги одного пайплайна с разной
    обработкой ошибок у вызывающего (`queue_loop.py`).

    Исключения (`asyncssh.Error`/`OSError`/таймаут коннекта, невалидный ключ)
    не перехватываются здесь — это ответственность вызывающего, симметрично
    тому, как `execute()` сам решает, что считать провалом SSH-уровня.
    """
    try:
        client_key = asyncssh.import_private_key(ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid SSH private key: {type(exc).__name__}") from exc

    conn = await asyncssh.connect(
        host=host,
        username=username,
        client_keys=[client_key],
        # Тот же принцип, что у execute() — стенды часто переустанавливаются.
        known_hosts=None,
        connect_timeout=connect_timeout,
        login_timeout=connect_timeout,
    )
    async with conn:
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(remote_path, "w") as f:
                await f.write(content)
            if mode:
                await sftp.chmod(remote_path, int(mode, 8))
