"""ipmitool CLI-клиент как fallback для BMC без Redfish.

Старые Supermicro X9/X10, IPMI 1.5/2.0 generic BMC и часть OEM-контроллеров
не отдают Redfish (либо отдают, но только subset, в котором нет AccountService
или ResetActionInfo). Для них единственный надёжный путь — запускать
системный `ipmitool` и парсить вывод.

Клиент строится поверх `asyncio.create_subprocess_exec`:

* argv формируется списком — ни одного shell-метасимвола в командной строке;
* `password` НЕ кладётся в argv: ipmitool читает его из переменной
  окружения `IPMI_PASSWORD` по флагу `-E`. На Linux argv процесса читается
  через `/proc/<pid>/cmdline` любым процессом с тем же UID, поэтому `-P <pwd>`
  засветил бы BMC-пароль в `ps` на время вызова. Env дочернего процесса
  таким образом не виден;
* `argv_safe` в `IpmitoolError` и в логах — копия argv (пароля в нём больше
  нет, но для пароля при `user set password` он подставляется в argv и
  маскируется), чтобы пароль не утёк в `task.last_error`, в
  `audit.details.error` и в stdout publisher'а.

Все методы async и не блокируют event loop: subprocess'ом управляет asyncio,
`communicate()` ждёт без занятого ожидания, timeout — через
`asyncio.wait_for` с `terminate()` + двухсекундный grace на `kill()`.

Реальные сообщения об ошибках от ipmitool — короткие, на английском, без
структурных кодов: `Error: Unable to establish IPMI v2 / RMCP+ session`,
`Set User Password command failed (user 2)`. Парсинг — на минимально
необходимом уровне: только `chassis_power_status` разбирает
`Chassis Power is on|off`. Остальное — успех по `returncode == 0`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Final, Literal

logger = logging.getLogger(__name__)

# Стандартный путь к ipmitool на Astra/Debian/Ubuntu. Если в проде ipmitool
# поставят в /opt/... — можно вынести в settings, пока константа.
_IPMITOOL_BIN: Final[str] = "ipmitool"

# Имя env-переменной, из которой ipmitool читает пароль по флагу `-E`.
# Так пароль не попадает в argv (а значит и в /proc/<pid>/cmdline и `ps`).
_IPMI_PASSWORD_ENV: Final[str] = "IPMI_PASSWORD"

# Таймаут по умолчанию: ipmitool с lanplus обычно отвечает за 1-3 секунды,
# 30s — широкий запас под флапающие BMC. На реальных мёртвых контроллерах
# ipmitool сам выходит за ~5s с `Unable to establish IPMI session`, но при
# половине пакетов потерянных и retry'ях RMCP+ может зависнуть.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0

# Grace перед SIGKILL после SIGTERM по таймауту.
_TERMINATE_GRACE_SECONDS: Final[float] = 2.0

# Парсер `Chassis Power is on` / `Chassis Power is off`.
_POWER_STATUS_RE: Final[re.Pattern[str]] = re.compile(
    r"Chassis Power is (?P<state>on|off)\b", re.IGNORECASE,
)

PowerAction = Literal["on", "off", "cycle", "reset", "soft"]
PowerState = Literal["on", "off"]


@dataclass
class IpmitoolError(Exception):
    """Ошибка вызова ipmitool.

    `returncode` — exit-status подпроцесса (0 = успех; ipmitool сам редко
    возвращает специфичные коды, обычно 1 на любую ошибку).
    `stderr` — текст stderr, уже прошедший через redact (если в нём попался
    `-P <pwd>` или URL-credentials).
    `argv_safe` — список аргументов с маскированным паролем (`***`). BMC-пароль
    в argv не попадает (передаётся через env), но `user set password <id>
    <newpass>` несёт новый пароль последним positional-аргументом — он
    маскируется. Используется в audit-details и логах.

    Класс — exception, помеченный `@dataclass`, чтобы `repr` был
    воспроизводимым (полезно для теста "no plaintext in error").
    """

    returncode: int
    stderr: str
    argv_safe: list[str]
    message: str = ""
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover — trivial
        argv_str = " ".join(self.argv_safe)
        msg = self.message or f"ipmitool exited with code {self.returncode}"
        return f"{msg}: cmd=[{argv_str}] stderr={self.stderr!r}"


class IpmitoolTimeout(IpmitoolError):
    """Подпроцесс не завершился в пределах `timeout`.

    Отдельный класс, чтобы `tasks/power.py` мог дифференцировать «BMC жив,
    но отказал в auth» (`IpmitoolError`) от «BMC не отвечает по сети»
    (`IpmitoolTimeout`) при выборе retry-стратегии.
    """


def _mask_password_in_argv(argv: list[str]) -> list[str]:
    """Вернуть копию `argv` с маскированным паролем.

    BMC-пароль в argv больше не кладётся (передаётся через env по `-E`),
    но `-P <pwd>` могла бы прийти из чужого argv (например, из stderr
    ipmitool, который сам себя логирует) — поэтому ветку оставляем.

    Также маскируется password, который попадает как часть команды
    `user set password <id> <newpass>` — последний аргумент в такой
    команде. Идентифицируем по последовательности `user set password`.
    """
    safe = list(argv)

    # `-P <pwd>` — на случай, если пароль попал в argv из стороннего источника.
    for i, arg in enumerate(safe):
        if arg == "-P" and i + 1 < len(safe):
            safe[i + 1] = "***"

    # `user set password <id> <newpass>` — последний positional argument.
    # `range(len(safe) - 4)` уже гарантирует, что `safe[i + 4]` в границах,
    # отдельный guard не нужен.
    for i in range(len(safe) - 4):
        if (
            safe[i] == "user"
            and safe[i + 1] == "set"
            and safe[i + 2] == "password"
        ):
            safe[i + 4] = "***"
            break

    return safe


class IpmitoolClient:
    """Async-обёртка над CLI `ipmitool`.

    Параметры подключения фиксируются в конструкторе и склеиваются в argv
    каждого вызова через `_base_args()`. Сам клиент stateless — нет
    persistent-сессии, как у Redfish; каждая команда — отдельный RMCP+
    handshake. Это медленнее (≈1s overhead), но проще: не нужно
    переподключаться при сетевом провале.

    Пример::

        client = IpmitoolClient("10.0.0.5", "ADMIN", "secret")
        state = await client.chassis_power_status()  # "on" | "off"
        await client.chassis_power_action("cycle")
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 623,
        interface: str = "lanplus",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.username = username
        self._password = password
        self.port = port
        self.interface = interface
        self.timeout = timeout

    # ── public API ────────────────────────────────────────────────────────

    async def chassis_power_status(self) -> PowerState:
        """`ipmitool chassis power status` → `"on" | "off"`.

        Stdout ipmitool: `Chassis Power is on` либо `Chassis Power is off`.
        Любой другой формат — `IpmitoolError` (battery-backed BMC может
        выдать `unknown`, но это редкий случай и до него мы доберёмся,
        когда увидим в проде).
        """
        rc, stdout, stderr = await self._run(["chassis", "power", "status"])
        if rc != 0:
            raise IpmitoolError(
                returncode=rc,
                stderr=_redact(stderr),
                argv_safe=_mask_password_in_argv(self._base_args() + ["chassis", "power", "status"]),
                message="ipmitool chassis power status failed",
            )
        match = _POWER_STATUS_RE.search(stdout)
        if not match:
            raise IpmitoolError(
                returncode=rc,
                stderr=_redact(stdout + stderr),
                argv_safe=_mask_password_in_argv(self._base_args() + ["chassis", "power", "status"]),
                message="unrecognized chassis power status output",
            )
        return match.group("state").lower()  # type: ignore[return-value]

    async def chassis_power_action(self, action: PowerAction) -> None:
        """`ipmitool chassis power {on|off|cycle|reset|soft}`.

        Все 5 действий поддерживаются всеми BMC IPMI 2.0; legacy IPMI 1.5
        не имеет `soft` (graceful shutdown) — на нём вернётся `IpmitoolError`
        с returncode=1, оператор должен использовать `off`.
        """
        if action not in {"on", "off", "cycle", "reset", "soft"}:
            raise ValueError(f"invalid power action: {action!r}")

        rc, _stdout, stderr = await self._run(["chassis", "power", action])
        if rc != 0:
            raise IpmitoolError(
                returncode=rc,
                stderr=_redact(stderr),
                argv_safe=_mask_password_in_argv(self._base_args() + ["chassis", "power", action]),
                message=f"ipmitool chassis power {action} failed",
            )

    async def user_set_password(self, user_id: int, new_password: str) -> None:
        """`ipmitool user set password <user_id> <new_password>`.

        ВАЖНО: `new_password` попадает в argv (вид. в `ps`), но не должен
        утекать в stdout-логи, audit и `task.last_error`. `_mask_password_in_argv`
        заменяет последний positional на `***` для всех вариантов
        сообщения об ошибке.
        """
        if user_id < 1 or user_id > 63:
            # IPMI 2.0 разрешает user_id 1..63; 1 — обычно anonymous null user.
            raise ValueError(f"invalid IPMI user_id: {user_id} (must be 1..63)")

        args = ["user", "set", "password", str(user_id), new_password]
        rc, _stdout, stderr = await self._run(args)
        if rc != 0:
            raise IpmitoolError(
                returncode=rc,
                stderr=_redact(stderr),
                argv_safe=_mask_password_in_argv(self._base_args() + args),
                message=f"ipmitool user set password {user_id} failed",
            )

    # ── internal ──────────────────────────────────────────────────────────

    def _base_args(self) -> list[str]:
        """Базовый префикс argv: -H <host> -p <port> -I <iface> -U <user> -E.

        Пароль не кладётся в argv — флаг `-E` говорит ipmitool взять его из
        env-переменной `IPMI_PASSWORD` (см. `_run`). Это убирает пароль из
        `/proc/<pid>/cmdline` и `ps`.

        Возвращается каждый раз новым списком — caller'ы (и
        `_mask_password_in_argv`) дописывают/мутируют свою копию.
        """
        return [
            "-H", self.host,
            "-p", str(self.port),
            "-I", self.interface,
            "-U", self.username,
            "-E",
        ]

    async def _run(self, sub_args: list[str]) -> tuple[int, str, str]:
        """Запустить ipmitool, дождаться завершения, вернуть (rc, stdout, stderr).

        Timeout через `asyncio.wait_for(communicate(), timeout)`. При
        таймауте — terminate() + 2s grace + kill(), затем
        `raise IpmitoolTimeout` (НЕ возвращаем как обычный exit-code,
        чтобы caller не путал `rc=124` от ipmitool с реальным таймаутом).
        """
        argv = [_IPMITOOL_BIN, *self._base_args(), *sub_args]
        argv_safe = _mask_password_in_argv(argv)

        logger.debug("Running ipmitool: %s", " ".join(argv_safe))

        # Пароль передаётся ребёнку через env (флаг `-E` его читает), а не
        # через argv — иначе он виден в `/proc/<pid>/cmdline`.
        #
        # Не наследуем полный env воркера: иначе ipmitool увидел бы
        # WORKER_BOT_TOKEN, LOGGING_SERVICE_API_KEY, DATABASE_URL и т.п.
        # При core-dump'е ipmitool'а или подмене бинаря (supply-chain)
        # эти секреты утекли бы. Передаём минимально необходимое: PATH
        # (без него exec и system-lib lookup сломаются), HOME/LANG/LC_ALL
        # — некоторые сборки ipmitool читают locale; всё остальное — пропускаем.
        child_env: dict[str, str] = {
            _IPMI_PASSWORD_ENV: self._password,
        }
        for key in ("PATH", "HOME", "LANG", "LC_ALL"):
            value = os.environ.get(key)
            if value is not None:
                child_env[key] = value

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except FileNotFoundError as exc:
            # ipmitool не установлен в контейнере. Считаем "network-level"
            # ошибкой — bmc_unreachable с понятным сообщением.
            raise IpmitoolError(
                returncode=-1,
                stderr=str(exc),
                argv_safe=argv_safe,
                message="ipmitool binary not found",
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            await self._kill_process(proc)
            raise IpmitoolTimeout(
                returncode=-1,
                stderr=f"ipmitool timed out after {self.timeout:.1f}s",
                argv_safe=argv_safe,
                message="ipmitool timeout",
            ) from None

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        return proc.returncode if proc.returncode is not None else -1, stdout, stderr

    async def _kill_process(self, proc: asyncio.subprocess.Process) -> None:
        """Грейсфул-завершение зависшего subprocess'а.

        terminate() (SIGTERM) → 2s ожидания → kill() (SIGKILL). Без grace
        ipmitool на mid-handshake может оставить дескриптор открытым; на
        Linux это не критично, но в k8s сборщик дескрипторов после
        OOMKill будет ругаться.
        """
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # После kill() сам wait() обычно возвращается мгновенно, но если
            # процесс «D»-state на mid-syscall (зависший сетевой сокет к BMC),
            # wait() висит бесконечно — это блокирует event-loop worker'а.
            # Cap на 5s грубый, но в k8s timeout'ы pod-shutdown'а ещё больше,
            # так что лучше потерять зомби, чем дедлокнуть весь worker.
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "ipmitool subprocess did not exit 5s after SIGKILL "
                    "(pid=%s); leaking process",
                    getattr(proc, "pid", "?"),
                )


def _redact(text: str) -> str:
    """Локальная маскировка пароля в stderr ipmitool.

    Глобальный `src/utils/redaction.py::redact_error_message` уже покрывает
    `-P <pwd>` через регэксп, но он применяется в `_runner` на свёрнутом
    сообщении. Здесь применяем дополнительно, чтобы пароль не попал даже в
    `IpmitoolError.stderr`-поле — этот объект может быть прологирован
    напрямую через `logger.exception(exc)` минуя redaction-слой.
    """
    if not text:
        return text
    try:
        from src.utils.redaction import redact_error_message
        return redact_error_message(text)
    except ImportError:  # pragma: no cover
        return text
