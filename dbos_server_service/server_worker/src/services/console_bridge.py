"""Интерактивная SSH-консоль: PTY-сессия + Redis pub/sub мост.

server_worker не держит собственного HTTP/WS-сервера, поэтому транспорт
между клиентским WebSocket'ом (в server_service) и удалённым shell'ом —
Redis pub/sub. server_service на connect генерит `session_id`, публикует
`start`-сигнал в control-канал, мостит клиентский WS на data-каналы:

  * `console:ctl:<sid>` — control: server_service шлёт `start` / `stop`,
    worker отвечает `ready` / `closed` / `error`.
  * `console:in:<sid>`  — ввод клиента (client → PTY stdin).
  * `console:out:<sid>` — вывод PTY (PTY stdout/stderr → client).

Сообщения в data-каналах — JSON `{"data": "<base64>"}`, base64 поверх
сырых байт, чтобы бинарный вывод терминала пережил JSON-транспорт.

Жизненный цикл сессии (`_ConsoleSession`):

  start → SSH connect под управляющим ключом (managed-сервер) →
  `open_pty` (invoke_shell) → две задачи-помпы (in→pty, pty→out) +
  watchdog idle/max-lifetime → teardown гарантированно закрывает
  SSH-канал и снимает подписки.

Логирование. Worker разбирает ввод по строкам (Enter) и эмитит
`ssh_console.command` через audit-outbox (тот же at-least-once путь, что
у task-handler'ов). `session_open`/`session_close` эмитит server_service
на стороне WS — здесь мы их не дублируем; worker фокусируется на
per-command аудите, который виден только ему (server_service сырой ввод
не получает).

Worker мастер-ключа SSH-аккаунтов не держит: console ходит исключительно
под управляющим ключом (`SSH_MANAGEMENT_PRIVATE_KEY_PATH`), сервер обязан
быть подготовлен. Это сознательная граница — пароль аккаунта в console-
поток не попадает.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.services import redis_pool
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Префиксы Redis-каналов. server_service строит их по тем же шаблонам —
# держать в sync (см. server_service console endpoint).
CTL_CHANNEL_PREFIX = "console:ctl:"
IN_CHANNEL_PREFIX = "console:in:"
OUT_CHANNEL_PREFIX = "console:out:"

# Pattern, на который подписывается control-listener: один psubscribe на все
# сессии, спавн `_ConsoleSession` на каждый `start`.
CTL_PATTERN = f"{CTL_CHANNEL_PREFIX}*"

# Сколько байт читаем из PTY за один read — баланс между latency (мелкий
# буфер = чаще publish) и overhead'ом. 4 KiB хватает на типичный кадр вывода.
_PTY_READ_BYTES = 4096


def ctl_channel(session_id: str) -> str:
    return f"{CTL_CHANNEL_PREFIX}{session_id}"


def in_channel(session_id: str) -> str:
    return f"{IN_CHANNEL_PREFIX}{session_id}"


def out_channel(session_id: str) -> str:
    return f"{OUT_CHANNEL_PREFIX}{session_id}"


def _validate_session_id(session_id: str) -> bool:
    """session_id должен быть безопасным для Redis-ключа/канала.

    server_service генерит его как `csn_<hex>`; здесь принимаем тот же
    POSIX-набор, что и task_id — никаких `*`/`:`/wildcards, иначе подписка
    могла бы перехватить чужие каналы.
    """
    return (
        isinstance(session_id, str)
        and 1 <= len(session_id) <= 64
        and all(c.isalnum() or c in "_-" for c in session_id)
    )


async def _emit_command_audit(
    *,
    command: str,
    session_id: str,
    server_id: str | None,
    department_id: str | None,
    actor_id: str | None,
    exit_status: int | None,
) -> None:
    """Записать `ssh_console.command` в audit-outbox (at-least-once).

    Идём через outbox, а не прямой `audit_client.emit`, чтобы команда не
    потерялась, если loging_service лёг, — тот же инвариант, что у
    task-handler'ов. `command` маскируется `redact_error_message` (URL-creds,
    `-P`, Bearer, JWT, `dbos_pat`/`bot` и т.п.) на случай, если оператор
    набрал секрет в строке.

    Severity: WARNING при ненулевом exit-коде (если он доступен), иначе
    дефолт (INFO) — не передаём severity, loging резолвит по `(action,
    status)`.
    """
    status = "failure" if exit_status not in (None, 0) else "success"
    details: dict = {
        "command": redact_error_message(command)[:1024],
        "session_id": session_id,
    }
    if server_id is not None:
        details["server_id"] = server_id
    if exit_status is not None:
        details["exit_status"] = exit_status
    payload: dict = {
        "action": "ssh_console.command",
        "status": status,
        "allowed": True,
        "actor_type": "user" if actor_id else "service",
        "target_type": "server",
    }
    if server_id is not None:
        payload["target_id"] = server_id
    if actor_id is not None:
        payload["actor_id"] = actor_id
    if department_id is not None:
        payload["department_id"] = department_id
    payload["details"] = details
    try:
        async with AsyncSessionLocal() as session:
            await task_repo.enqueue_audit(session, task_id=None, payload=payload)
            await session.commit()
    except Exception:  # noqa: BLE001 — аудит не должен ронять live-сессию
        logger.warning(
            "console: failed to enqueue command audit for session %s",
            session_id,
            exc_info=True,
        )


@dataclass
class _ConsoleSession:
    """Одна живая PTY-сессия: SSH invoke_shell + Redis-помпы + watchdog."""

    session_id: str
    host: str
    port: int
    management_user: str
    key_path: str
    server_id: str | None = None
    department_id: str | None = None
    actor_id: str | None = None
    idle_timeout: float = 900.0
    max_lifetime: float = 3600.0
    max_command_length: int = 8192

    _ssh: SshClient | None = field(default=None, init=False)
    _process: object | None = field(default=None, init=False)
    _last_input_at: float = field(default=0.0, init=False)
    _line_buffer: bytearray = field(default_factory=bytearray, init=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    async def run(self) -> str:
        """Поднять сессию и мостить, пока она жива. Вернуть reason закрытия.

        Reason из множества: `client_stop` (пришёл `stop`), `idle_timeout`,
        `max_lifetime`, `pty_eof` (shell завершился), `error` (SSH/Redis сбой).
        Teardown в `finally` гарантирует закрытие SSH-канала и публикацию
        `closed` в control-канал.
        """
        self._last_input_at = time.monotonic()
        reason = "error"
        try:
            self._ssh = SshClient(
                host=self.host,
                username=self.management_user,
                password=None,
                port=self.port,
                client_keys=[self.key_path],
            )
            await self._ssh.connect()
            self._process = await self._ssh.open_pty()
            await self._publish_ctl({"event": "ready"})
            reason = await self._pump()
        except SshError as exc:
            await self._publish_ctl(
                {"event": "error", "error_code": exc.error_code},
            )
            logger.warning(
                "console session %s ssh error: %s",
                self.session_id, exc.error_code,
            )
            reason = "error"
        except Exception as exc:  # noqa: BLE001
            await self._publish_ctl({"event": "error", "error_code": "CONSOLE_INTERNAL"})
            logger.warning(
                "console session %s failed: %s",
                self.session_id,
                redact_error_message(f"{type(exc).__name__}: {exc}"),
                exc_info=True,
            )
            reason = "error"
        finally:
            await self._teardown(reason)
        return reason

    async def _pump(self) -> str:
        """Запустить помпы in→pty, pty→out и watchdog; ждать первого финиша."""
        in_task = asyncio.create_task(self._pump_input(), name=f"console-in-{self.session_id}")
        out_task = asyncio.create_task(self._pump_output(), name=f"console-out-{self.session_id}")
        wd_task = asyncio.create_task(self._watchdog(), name=f"console-wd-{self.session_id}")
        tasks = [in_task, out_task, wd_task]
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # reason несёт та задача, что финишировала первой.
            for t in done:
                res = t.result() if not t.cancelled() and t.exception() is None else None
                if isinstance(res, str):
                    return res
            return "pty_eof"
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _pump_input(self) -> str:
        """Подписаться на `console:in:<sid>`, писать в PTY, разбирать команды."""
        client = redis_pool.get_pubsub_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(in_channel(self.session_id))
        try:
            async for message in pubsub.listen():
                if self._stop.is_set():
                    return "client_stop"
                if message is None or message.get("type") != "message":
                    continue
                data = _decode_frame(message.get("data"))
                if data is None:
                    continue
                self._last_input_at = time.monotonic()
                self._accumulate_and_audit(data)
                proc = self._process
                if proc is not None:
                    proc.stdin.write(data)
            return "pty_eof"
        finally:
            try:
                await pubsub.unsubscribe(in_channel(self.session_id))
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("console: input pubsub close failed", exc_info=True)

    def _accumulate_and_audit(self, data: bytes) -> None:
        """Накопить ввод и заэмитить `ssh_console.command` на каждую строку.

        Команда — текст между Enter'ами (`\\n` или `\\r`). Возврат каретки и
        перевод строки нормализуем. Слишком длинная строка (нет Enter'а
        дольше лимита) форс-флашится, чтобы не копить память на ботообразном
        вводе — она уходит в аудит с пометкой truncated.
        """
        for byte in data:
            ch = bytes([byte])
            if ch in (b"\n", b"\r"):
                self._flush_command_line()
            else:
                self._line_buffer += ch
                if len(self._line_buffer) >= self.max_command_length:
                    self._flush_command_line(truncated=True)

    def _flush_command_line(self, *, truncated: bool = False) -> None:
        if not self._line_buffer:
            return
        raw = bytes(self._line_buffer)
        self._line_buffer.clear()
        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            text = "<undecodable>"
        if not text:
            return
        if truncated:
            text = text + " …<truncated>"
        # Аудит — fire-and-forget task, чтобы не блокировать ввод; ошибки
        # глушатся внутри `_emit_command_audit`.
        asyncio.create_task(
            _emit_command_audit(
                command=text,
                session_id=self.session_id,
                server_id=self.server_id,
                department_id=self.department_id,
                actor_id=self.actor_id,
                exit_status=None,
            ),
            name=f"console-audit-{self.session_id}",
        )

    async def _pump_output(self) -> str:
        """Читать PTY stdout, публиковать в `console:out:<sid>`."""
        client = redis_pool.get_redis()
        proc = self._process
        if proc is None:
            return "error"
        while not self._stop.is_set():
            try:
                chunk = await proc.stdout.read(_PTY_READ_BYTES)
            except Exception:  # noqa: BLE001
                return "pty_eof"
            if not chunk:
                return "pty_eof"
            await client.publish(
                out_channel(self.session_id),
                _encode_frame(chunk),
            )
        return "client_stop"

    async def _watchdog(self) -> str:
        """Снести сессию по idle-таймауту, max-lifetime или `stop`-событию."""
        started = time.monotonic()
        while True:
            if self._stop.is_set():
                return "client_stop"
            now = time.monotonic()
            if now - self._last_input_at >= self.idle_timeout:
                return "idle_timeout"
            if now - started >= self.max_lifetime:
                return "max_lifetime"
            # Просыпаемся достаточно часто, чтобы среагировать на stop без
            # лишнего ожидания, но не крутим busy-loop.
            await asyncio.sleep(min(1.0, self.idle_timeout))

    def signal_stop(self) -> None:
        """Внешний сигнал `stop` из control-канала — поднимаем event."""
        self._stop.set()

    async def _teardown(self, reason: str) -> None:
        """Гарантированно закрыть PTY + SSH-канал и опубликовать `closed`.

        Идемпотентно: повторный вызов не падает. Любые ошибки закрытия —
        посмертный шум, глушим в debug.
        """
        proc = self._process
        self._process = None
        if proc is not None:
            try:
                proc.close()
                await proc.wait_closed()
            except Exception:  # noqa: BLE001
                logger.debug("console: pty close failed", exc_info=True)
        if self._ssh is not None:
            try:
                await self._ssh.close()
            except Exception:  # noqa: BLE001
                logger.debug("console: ssh close failed", exc_info=True)
            self._ssh = None
        await self._publish_ctl({"event": "closed", "reason": reason})

    async def _publish_ctl(self, message: dict) -> None:
        try:
            client = redis_pool.get_redis()
            await client.publish(ctl_channel(self.session_id), json.dumps(message))
        except Exception:  # noqa: BLE001
            logger.debug("console: ctl publish failed", exc_info=True)


def _encode_frame(data: bytes) -> str:
    return json.dumps({"data": base64.b64encode(data).decode("ascii")})


def _decode_frame(raw) -> bytes | None:
    """Распаковать `{"data": "<base64>"}` обратно в сырые байты.

    Принимает str / bytes (redis-py отдаёт по настройкам клиента). Битый
    кадр — None (мост его пропускает, не валит сессию).
    """
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        obj = json.loads(raw)
        b64 = obj.get("data")
        if not isinstance(b64, str):
            return None
        return base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return None


def _build_session_from_start(session_id: str, msg: dict) -> _ConsoleSession:
    """Собрать `_ConsoleSession` из `start`-control-сообщения.

    Поднимает `SshError(SSH_MANAGEMENT_KEY_MISSING)` если управляющий ключ
    не сконфигурирован — console работает только по ключу на managed-сервере.
    """
    settings = get_settings()
    key_path = settings.ssh_management_private_key_path
    if not key_path:
        raise SshError(
            error_code="SSH_MANAGEMENT_KEY_MISSING",
            host=str(msg.get("host") or ""),
            message="server is managed but SSH_MANAGEMENT_PRIVATE_KEY_PATH is not configured",
        )
    return _ConsoleSession(
        session_id=session_id,
        host=str(msg.get("host") or msg.get("server_id") or ""),
        port=int(msg.get("ssh_port") or 22),
        management_user=str(msg.get("management_user") or settings.ssh_management_user),
        key_path=key_path,
        server_id=msg.get("server_id"),
        department_id=msg.get("target_department_id") or msg.get("department_id"),
        actor_id=msg.get("actor_id"),
        idle_timeout=settings.console_idle_timeout_seconds,
        max_lifetime=settings.console_max_session_seconds,
        max_command_length=settings.console_max_command_length,
    )


# Активные сессии процесса: session_id → (task, session). Нужно, чтобы
# `stop`-сигнал нашёл живую сессию и поднял её stop-event, а shutdown мог
# дренировать всё разом.
_ACTIVE_SESSIONS: dict[str, tuple[asyncio.Task, _ConsoleSession]] = {}


async def run_control_listener() -> None:
    """Фоновый loop: psubscribe `console:ctl:*`, спавн сессий на `start`.

    Запускается из `WORKER_STARTUP` (как audit/dispatch publisher loops).
    Слушает control-канал всех сессий: `start` → новая `_ConsoleSession`
    в отдельной task'е; `stop` → сигнал существующей. Сам loop переживает
    транзиентные сбои Redis (re-subscribe), завершается только на отмене
    (WORKER_SHUTDOWN).
    """
    while True:
        pubsub = None
        try:
            client = redis_pool.get_pubsub_redis()
            pubsub = client.pubsub()
            await pubsub.psubscribe(CTL_PATTERN)
            async for message in pubsub.listen():
                if message is None or message.get("type") != "pmessage":
                    continue
                await _handle_ctl_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "console control listener error, re-subscribing", exc_info=True,
            )
            await asyncio.sleep(1.0)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:  # noqa: BLE001
                    logger.debug("console: ctl listener pubsub close failed", exc_info=True)


async def _handle_ctl_message(message: dict) -> None:
    """Разобрать одно control-сообщение из `console:ctl:*`."""
    channel = message.get("channel")
    if isinstance(channel, (bytes, bytearray)):
        channel = channel.decode("utf-8", errors="replace")
    if not isinstance(channel, str) or not channel.startswith(CTL_CHANNEL_PREFIX):
        return
    session_id = channel[len(CTL_CHANNEL_PREFIX):]
    if not _validate_session_id(session_id):
        return
    raw = message.get("data")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        msg = json.loads(raw)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(msg, dict):
        return
    action = msg.get("action")
    if action == "start":
        _start_session(session_id, msg)
    elif action == "stop":
        entry = _ACTIVE_SESSIONS.get(session_id)
        if entry is not None:
            entry[1].signal_stop()


def _start_session(session_id: str, msg: dict) -> None:
    """Спавн новой сессии. Дубль (тот же sid) игнорируется."""
    if session_id in _ACTIVE_SESSIONS:
        return
    try:
        session = _build_session_from_start(session_id, msg)
    except SshError as exc:
        # Конфиг не готов (нет ключа) — отвечаем error в control, не спавним.
        asyncio.create_task(
            _publish_start_error(session_id, exc.error_code),
            name=f"console-starterr-{session_id}",
        )
        return

    async def _runner() -> None:
        try:
            await session.run()
        finally:
            _ACTIVE_SESSIONS.pop(session_id, None)

    task = asyncio.create_task(_runner(), name=f"console-session-{session_id}")
    _ACTIVE_SESSIONS[session_id] = (task, session)


async def _publish_start_error(session_id: str, error_code: str) -> None:
    try:
        client = redis_pool.get_redis()
        await client.publish(
            ctl_channel(session_id),
            json.dumps({"event": "error", "error_code": error_code}),
        )
    except Exception:  # noqa: BLE001
        logger.debug("console: start-error publish failed", exc_info=True)


async def drain_sessions(timeout: float = 5.0) -> None:
    """Снести все живые сессии на graceful shutdown.

    Поднимаем stop-event каждой, ждём завершения их task'ей до `timeout`.
    Оставшееся — отменяем. Best-effort: shutdown не должен зависнуть.
    """
    entries = list(_ACTIVE_SESSIONS.values())
    for _task, session in entries:
        session.signal_stop()
    tasks = [t for t, _s in entries if not t.done()]
    if not tasks:
        return
    try:
        await asyncio.wait(tasks, timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.debug("console: drain wait failed", exc_info=True)
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
