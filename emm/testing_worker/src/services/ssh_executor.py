"""Исполнение резолвленной команды теста на стенде по SSH (§5, §5.5, §8 плана миграции).

`command`, приходящий от `testing_service` (`POST /internal/queue/claim`),
уже полностью резолвлен конструктором команд (`resolve_command` на стороне
`testing_service`) в `list[str]` — обычный argv, без сборки строки наивной
конкатенацией.

SSH exec-канал, в отличие от `subprocess`, не умеет запустить "argv без
интерпретации shell'ом" — `SSHClientConnection`, что для однократного `run()`,
что для потокового `create_process()`, несёт одну command-строку, которую
удалённый sshd передаёт login-шеллу пользователя (`sh -c '<строка>'`).
Эквивалент требования "без `shell=True`" здесь — не пропустить сборку
строки, а собрать её безопасно: `shlex.join(command)` экранирует каждый
аргумент по отдельности, поэтому пробел/`;`/`&&`/`$(...)` внутри значения
одного слота не может развалиться в отдельную shell-команду или изменить
границы аргументов. Это осознанная замена, не пропущенный шаг — naive
`" ".join(command)` был бы инъекцией, `shlex.join` — нет.

Исполнение — потоковое (§8.6 плана: живой лог в консоли сервера строится
поверх того же накопленного текста): `conn.create_process()` вместо
блокирующего `conn.run()`, stdout и stderr сведены в один канал
(`stderr=asyncssh.STDOUT`) — тот же принцип, что у легаси `CONCLUSION:`,
единый связный вывод, а не раздельные потоки. Вывод копится целиком в
памяти (нужен полностью для итогового `log-segment`) и одновременно
отдаётся наружу нарастающими кусками через `on_output_chunk` — не чаще
`chunk_interval_seconds` и не крупнее `chunk_max_bytes` за раз, что раньше
сработает.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
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


@dataclass
class ExecutionResult:
    """Итог одной попытки исполнения — от коннекта до завершения процесса.

    `connected=False` значит до запуска команды дело не дошло (SSH-уровня
    провал: ключ/аутентификация/таймаут коннекта) — `output`/`started_at`/
    `finished_at` в этом случае пустые, вызывающий не заводит `log-segment`
    на этот случай, только `completed(succeeded=False)`.

    `connected=True` значит команда реально стартовала — независимо от того,
    как она в итоге завершилась (успех, ненулевой код, таймаут исполнения,
    обрыв соединения на середине). `output` несёт всё, что успело
    накопиться, даже при провале.
    """

    connected: bool
    succeeded: bool
    exit_code: int | None
    error: str | None
    output: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


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


async def _stream_process_output(process, accumulator: _ChunkAccumulator) -> None:
    """Читает merged stdout+stderr до EOF, кусками, скармливая аккумулятору."""
    while True:
        chunk = await process.stdout.read(_READ_SIZE)
        if chunk == "":
            break
        await accumulator.add(chunk)


async def execute(
    host: str,
    test_username: str,
    test_ssh_private_key: str,
    command: list[str],
    *,
    connect_timeout: float = 30.0,
    command_timeout: float = 3600.0,
    on_output_chunk: OnOutputChunk | None = None,
    chunk_max_bytes: int = DEFAULT_CHUNK_MAX_BYTES,
    chunk_interval_seconds: float = DEFAULT_CHUNK_INTERVAL_SECONDS,
) -> ExecutionResult:
    """Подключиться к `host` по ключу и потоково исполнить `command`.

    Никакого fallback на `test_password` при провале ключа — по контракту
    `testing_service` ключ приходит заполненным всегда, а протокольный
    провал ключа фиксируется как обычный провал, не повод менять способ
    аутентификации на лету.
    """
    try:
        client_key = asyncssh.import_private_key(test_ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        logger.warning("ssh_executor: invalid private key for host=%s: %s", host, type(exc).__name__)
        return ExecutionResult(
            connected=False, succeeded=False, exit_code=None,
            error=f"invalid SSH private key: {type(exc).__name__}",
        )

    cmd_str = shlex.join(command)

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

    async with conn:
        try:
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
        try:
            await asyncio.wait_for(_stream_process_output(process, accumulator), timeout=command_timeout)
            result = await process.wait()
            exit_status = result.exit_status
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("ssh_executor: command timed out for host=%s: %s", host, type(exc).__name__)
            run_error = f"SSH command timed out: {type(exc).__name__}"
        except asyncssh.Error as exc:
            logger.warning("ssh_executor: asyncssh error running command on host=%s: %s", host, type(exc).__name__)
            run_error = f"SSH run error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN]
        finally:
            stop_ticker.set()
            await ticker_task
            await accumulator.flush()

    finished_at = datetime.now(timezone.utc)
    output = accumulator.full_output

    if run_error is not None:
        return ExecutionResult(
            connected=True, succeeded=False, exit_code=None, error=run_error,
            output=output, started_at=started_at, finished_at=finished_at,
        )

    if exit_status == 0:
        return ExecutionResult(
            connected=True, succeeded=True, exit_code=0, error=None,
            output=output, started_at=started_at, finished_at=finished_at,
        )

    error = _tail(output.strip(), _ERROR_TAIL_MAX_LEN) or f"command exited with status {exit_status}"
    return ExecutionResult(
        connected=True, succeeded=False, exit_code=exit_status, error=error,
        output=output, started_at=started_at, finished_at=finished_at,
    )


# Прибиваем тест по имени скрипта, а не по PID: PID у нас нет — sshd запускает
# команду через login-шелл, и `sudo` порождает собственное дерево процессов,
# до которого обрыв SSH-канала не доходит. На стенде в любой момент идёт не
# больше одного `starter.sh` (очередь стенда сериализована самим дизайном
# testing_service), так что `pkill -f` по имени не заденет чужую работу.
_KILL_COMMAND = "sudo pkill -f starter.sh"


async def kill_remote_process(
    host: str,
    test_username: str,
    test_ssh_private_key: str,
    *,
    connect_timeout: float = 30.0,
) -> bool:
    """Оборвать идущий на стенде `starter.sh` отдельным коротким SSH-коннектом.

    Отдельное соединение, а не тот же канал, на котором висит `execute()` —
    тот занят чтением вывода до EOF и послать по нему ещё одну команду нельзя.

    Best-effort: любая SSH-ошибка (стенд не отвечает, ключ протух, сеть легла)
    не поднимается наружу — возвращается `False` и пишется WARNING. Смысл в
    том, что вызывающий всё равно обрывает свою сторону: незакрытый процесс на
    стенде в худшем случае доживёт до собственного таймаута, но очередь на
    этом стоять не должна. `True` — команда ушла (ненулевой код `pkill`, когда
    процесса уже нет, ошибкой не считается).
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
            await conn.run(_KILL_COMMAND, check=False)
    except (asyncssh.Error, OSError, asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor.kill: %r failed on host=%s: %s", _KILL_COMMAND, host, type(exc).__name__)
        return False

    logger.info("ssh_executor.kill: %r sent to host=%s", _KILL_COMMAND, host)
    return True


async def write_remote_file(
    host: str,
    username: str,
    ssh_private_key: str,
    remote_path: str,
    content: str,
    *,
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
