"""Реальный SSH-клиент на ``asyncssh``.

Используется handler'ами `inventory.sync` (collect facts) и
`account.rotate_password` (chpasswd). Под капотом — короткоживущая обёртка
над `asyncssh.connect`, без pool'инга (one connection per task).

Дизайн.

* Класс `SshClient` — короткоживущий wrapper: один экземпляр на одну
  операцию (one connection per task). Это просто и безопасно: pool'я
  по `(host, user)` нет, кэшировать долгоживущие соединения к BMC/Linux-
  host'ам через NAT/firewall в кластере — лишний риск (idle drop,
  half-closed sockets).
* `password` авторизация — основной путь (server_account flow). Если
  caller передаёт `client_keys` — поддерживаем key-based; для
  inventory это будущая фича (worker подкладывает свой ключ через
  k8s secret).
* host-key проверка управляется настройкой `SSH_STRICT_HOST_KEY_CHECKING`
  (`Settings.ssh_strict_host_key_checking`, дефолт `True`). При strict-режиме
  `known_hosts` обязан быть прокинут caller'ом, иначе соединение отклоняется
  (`SshError(error_code='SSH_STRICT_NO_HOST_KEY')`) — `known_hosts=None`
  у asyncssh означает «принять любой host-key без проверки», что открывает
  MITM. Отключать (`false`) можно только в доверенных dev/test-сетях, где
  known_hosts ещё негде взять; в production validator не даёт его выключить.
* `timeout=30` — общий cap на коннект + handshake. Реальный inventory
  занимает 2-5 секунд на современном железе; 30s — щедрый запас под
  laggy uplink.
* Все ошибки — `SshError`. `password` и `new_password` НЕ попадают в
  exception args, repr или `host`/`cmd_sanitized` — это критично, иначе
  redact-regex'ы в `utils/redaction.py` не успеют отсечь утечку (см.
  `tests/test_ssh_client.py::TestPasswordSanitization`).

Sudo:

* `run(cmd, sudo=True)` исполняет `sudo -S -p '' <cmd>` и подаёт пароль
  на stdin (одна строка + LF). `-S` читает с stdin, `-p ''` подавляет
  prompt — иначе строка «[sudo] password for ...» попадает в stderr и
  путает caller'а.
* Если sudo не настроен NOPASSWD и пароль неправильный — sudo вернёт
  exit-code != 0 и `Sorry, try again.` в stderr. Мы поднимаем `SshError`
  с `returncode` и redacted stderr (плейсхолдер пароля заменяется).

`chpasswd` контракт:

* `set_password(login, new_password)` пишет `"{login}:{new_password}\n"`
  на stdin процесса `sudo chpasswd`. `chpasswd` атомарно меняет
  /etc/shadow. Если sudo требует пароль — он подаётся первой строкой
  до payload'а (см. `_run_with_stdin`).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import asyncssh

from src.core.config import get_settings

logger = logging.getLogger(__name__)


# ── Errors ───────────────────────────────────────────────────────────────────


@dataclass
class SshError(Exception):
    """Структурированная ошибка SSH-операции.

    `host` — DNS / IP сервера. Не содержит креды (host-string sanitized
    caller'ом, мы не префиксуем `user@`).

    `cmd_sanitized` — команда БЕЗ `new_password` / `password` substring'ов.
    `set_password` подменяет login+password на плейсхолдер ДО конструкции
    исключения; `run(sudo=True)` подменяет sudo password.

    `returncode` — None если коннект/handshake/timeout (impl не успел
    запустить команду), int если процесс стартовал и завершился.

    `stderr` — прогнан через `utils/redaction.redact_error_message`
    перед попаданием сюда (вызывается caller'ом или этим модулем).
    """

    error_code: str
    host: str
    cmd_sanitized: str = ""
    returncode: int | None = None
    stderr: str = ""
    message: str = ""
    # Не использовать __init_subclass__ / __post_init__ — dataclass не
    # пробрасывает позиционный message в Exception.args, поэтому делаем
    # explicit __str__.
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (
            f"{self.error_code}: host={self.host} rc={self.returncode} "
            f"cmd={self.cmd_sanitized!r} stderr={self.stderr!r}"
        )


# ── SshClient ────────────────────────────────────────────────────────────────


_STRICT_ENV = "SSH_STRICT_HOST_KEY_CHECKING"


def _is_strict_mode() -> bool:
    """Strict-проверка known_hosts (дефолт True, см. `Settings`).

    `True` — отказ соединяться при пустом `known_hosts` (см.
    `SshClient.connect`); `False` — accept-any (только доверенные dev/test).
    Управляется env `SSH_STRICT_HOST_KEY_CHECKING` через `Settings`.
    """
    return get_settings().ssh_strict_host_key_checking


# Валидация login'а перед передачей в chpasswd. Принимаем только
# безопасный POSIX-набор: буквы, цифры, точка, подчёркивание, дефис.
# Это защита от инъекции — символ `:` (разделитель payload'а chpasswd),
# `\n`, `;`, `$`, пробелы и shell-метасимволы не проходят.
#
# Держим в sync с `server_service/src/schemas/server_account.py`
# (поле `ServerAccountCreate.login`, тот же pattern). Дублируем, потому
# что worker не должен полагаться на то, что server_service пропустил
# валидный login — это defence-in-depth перед `chpasswd`. Если меняешь
# pattern — меняй в обоих местах и обнови тесты.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

# Имя Unix-группы — тот же POSIX-набор, что и login.
_GROUP_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

# Путь shell'а / home-директории для useradd/usermod. Аргумент команды, не
# stdin, поэтому набор строже: буквы, цифры, `/`, `.`, `_`, `-`. Без пробелов
# и shell-метасимволов — защита от инъекции.
_PATH_RE = re.compile(r"^[A-Za-z0-9/._\-]+$")


class SshClient:
    """Wrapper над `asyncssh.connect` с тремя бизнес-операциями.

    Один экземпляр = одно подключение. Использовать как async context
    manager:

        async with SshClient(host, user, pwd) as ssh:
            rc, out, err = await ssh.run("uname -a")

    Прямой вызов `await ssh.connect()` / `await ssh.close()` тоже
    допустим, но менее aesthetic.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str | None = None,
        *,
        port: int = 22,
        known_hosts: str | None = None,
        timeout: float = 30.0,
        client_keys: list[str] | None = None,
    ) -> None:
        self.host = host
        self.username = username
        self._password = password
        self.port = port
        self._known_hosts = known_hosts
        self.timeout = timeout
        self._client_keys = client_keys
        self._conn: asyncssh.SSHClientConnection | None = None

    async def __aenter__(self) -> "SshClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        """Открыть SSH-соединение. Идемпотентно (повторный connect — no-op).

        Решение по host-key:

        * `known_hosts=<path>` → asyncssh валидирует fingerprint;
        * `known_hosts=None` со strict (дефолт) → отказ, SshError;
        * `known_hosts=None` без strict → accept-any (только dev/test).
        """
        if self._conn is not None:
            return

        if self._known_hosts is None and _is_strict_mode():
            raise SshError(
                error_code="SSH_STRICT_NO_HOST_KEY",
                host=self.host,
                message=(
                    f"{_STRICT_ENV}=true requires known_hosts to be set; "
                    "refusing accept-any-host connection"
                ),
            )

        try:
            self._conn = await asyncssh.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self._password,
                client_keys=self._client_keys,
                known_hosts=self._known_hosts,
                connect_timeout=self.timeout,
                login_timeout=self.timeout,
            )
        except asyncssh.PermissionDenied as exc:
            raise SshError(
                error_code="SSH_AUTH_FAILED",
                host=self.host,
                message=f"authentication failed: {type(exc).__name__}",
            ) from exc
        except (TimeoutError, asyncssh.ConnectionLost, OSError) as exc:
            raise SshError(
                error_code="SSH_CONNECT_FAILED",
                host=self.host,
                message=f"connect failed: {type(exc).__name__}: {exc}",
            ) from exc
        except asyncssh.Error as exc:
            raise SshError(
                error_code="SSH_CONNECT_FAILED",
                host=self.host,
                message=f"asyncssh error: {type(exc).__name__}",
            ) from exc

    async def close(self) -> None:
        """Закрыть SSH-соединение. Идемпотентно."""
        if self._conn is None:
            return
        try:
            self._conn.close()
            await self._conn.wait_closed()
        except Exception:  # noqa: BLE001
            # Closing failure — это уже посмертный шум, не критично.
            logger.debug("error during ssh close to %s", self.host, exc_info=True)
        finally:
            self._conn = None

    # ── Core: run a command ──────────────────────────────────────────────

    async def run(
        self,
        command: str,
        *,
        sudo: bool = False,
        stdin_payload: str | None = None,
    ) -> tuple[int, str, str]:
        """Выполнить shell-команду на удалённом хосте.

        `sudo=True` → команда оборачивается в `sudo -S -p ''`. Если у
        клиента задан `password`, sudo получает его на stdin первой
        строкой; после — опциональный `stdin_payload` (для chpasswd).

        Возврат: `(returncode, stdout, stderr)`. Stdout/stderr — str
        (asyncssh декодирует utf-8 по умолчанию). На любую ошибку
        SSH-уровня — `SshError` (а НЕ tuple с rc=-1: caller иначе
        затрёт детали).

        Текстовое содержимое stderr НЕ модифицируется здесь — caller
        отвечает за redaction. `set_password` ниже это делает, прямой
        `run` оставлен «прозрачным» для inventory-команд.
        """
        if self._conn is None:
            raise SshError(
                error_code="SSH_NOT_CONNECTED",
                host=self.host,
                cmd_sanitized=_sanitize_cmd(command),
                message="run() called before connect()",
            )

        cmd_to_run = command
        stdin_full = stdin_payload
        if sudo:
            cmd_to_run = f"sudo -S -p '' {command}"
            # sudo password подаётся первой строкой stdin, payload
            # (если есть) — после, разделено LF. Это позволяет одним
            # вызовом покрыть chpasswd: первая строка — пароль для sudo,
            # вторая — `login:newpwd` для chpasswd.
            sudo_pwd = self._password or ""
            stdin_full = f"{sudo_pwd}\n" + (stdin_payload or "")

        try:
            result = await self._conn.run(
                cmd_to_run,
                input=stdin_full,
                check=False,
            )
        except asyncssh.TimeoutError as exc:
            raise SshError(
                error_code="SSH_TIMEOUT",
                host=self.host,
                cmd_sanitized=_sanitize_cmd(command),
                message=f"command timed out: {exc}",
            ) from exc
        except asyncssh.Error as exc:
            raise SshError(
                error_code="SSH_RUN_FAILED",
                host=self.host,
                cmd_sanitized=_sanitize_cmd(command),
                message=f"run failed: {type(exc).__name__}",
            ) from exc

        # asyncssh возвращает bytes если encoding=None, str иначе.
        # Default — utf-8 str; для defensive cast — coerce None в "".
        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", errors="replace")
        rc = result.exit_status if result.exit_status is not None else -1
        return rc, stdout, stderr

    # ── Password rotation: chpasswd via sudo ─────────────────────────────

    async def set_password(self, login: str, new_password: str) -> None:
        """Сменить пароль пользователя `login` через `sudo chpasswd`.

        `chpasswd` читает строки вида `username:newpassword` со stdin.
        Мы передаём ровно одну: `f"{login}:{new_password}\\n"`. sudo
        получает свой пароль (текущий пароль клиента) первой строкой
        выше — см. `run(sudo=True, stdin_payload=...)`.

        Контракт безопасности:

        * `new_password` НЕ попадает ни в `command` (только stdin), ни
          в `cmd_sanitized` исключения. `login` — попадает только в
          `cmd_sanitized` после sanitization (только символы [a-zA-Z0-9_.-]).
        * Если chpasswd падает (rc!=0), `stderr` прогоняется через
          `redact_error_message` ниже по стеку (`_runner` вызывает на
          `last_error`). Здесь дополнительно вырезаем сам `login:pwd`
          payload из stderr на случай, если chpasswd его echo'нул.
        """
        if not _LOGIN_RE.match(login):
            raise SshError(
                error_code="SSH_INVALID_LOGIN",
                host=self.host,
                cmd_sanitized="chpasswd",
                message=f"login {login!r} contains characters disallowed for chpasswd",
            )

        # Сам payload — `login:newpwd\n`. Не логируется, не попадает в
        # cmd_sanitized.
        payload = f"{login}:{new_password}\n"
        rc, stdout, stderr = await self.run(
            "chpasswd",
            sudo=True,
            stdin_payload=payload,
        )
        if rc != 0:
            # Подчищаем stderr — защита от случая, когда chpasswd
            # вернул в логе сам логин/пароль (теоретически он не
            # должен, но не доверяем).
            stderr_clean = _scrub_password_echo(stderr, new_password)
            raise SshError(
                error_code="SSH_CHPASSWD_FAILED",
                host=self.host,
                cmd_sanitized=f"chpasswd <{login}>",
                returncode=rc,
                stderr=stderr_clean,
                message=f"chpasswd exit code {rc}",
            )

    # ── OS-user lifecycle: useradd / usermod / userdel ───────────────────

    async def user_exists(self, login: str) -> bool:
        """True если пользователь с таким login'ом есть в passwd-базе.

        `getent passwd <login>` возвращает rc=0 если пользователь найден,
        rc=2 если нет. Используется для idempotency: provision не падает на
        уже-существующем, deprovision — на отсутствующем.
        """
        if not _LOGIN_RE.match(login):
            raise SshError(
                error_code="SSH_INVALID_LOGIN",
                host=self.host,
                cmd_sanitized="getent passwd",
                message=f"login {login!r} contains disallowed characters",
            )
        rc, _out, _err = await self.run(f"getent passwd {login}")
        return rc == 0

    async def create_user(
        self,
        login: str,
        *,
        new_password: str | None = None,
        groups: list[str] | None = None,
        has_sudo: bool = False,
        shell: str | None = None,
        home_dir: str | None = None,
    ) -> None:
        """Завести OS-пользователя через `useradd` + опционально задать пароль.

        Idempotent: если пользователь уже существует — выходим без ошибки
        (provision повторяемо). Иначе собираем `useradd` с `-m` (создать home),
        `-s <shell>`, `-d <home>`, `-G <groups>` (sudo-группа доклеивается при
        `has_sudo`). После create'а, если задан `new_password`, ставим его
        через `chpasswd` (тот же путь, что `set_password`).

        Безопасность: все аргументы (login/shell/home/groups) валидируются
        regex'ами до подстановки в команду — это защита от shell-инъекции.
        `new_password` идёт только на stdin chpasswd, в командную строку и в
        `cmd_sanitized` исключений не попадает.
        """
        self._validate_login(login)
        if await self.user_exists(login):
            # Уже на месте — ничего не делаем, но при наличии пароля/атрибутов
            # синхронизируем их (как usermod), чтобы повтор был осмысленным.
            await self.modify_user(
                login, groups=groups, has_sudo=has_sudo, shell=shell,
            )
            if new_password is not None:
                await self.set_password(login, new_password)
            return

        opts = ["-m"]
        if shell is not None:
            opts += ["-s", self._safe_path(shell, "shell")]
        if home_dir is not None:
            opts += ["-d", self._safe_path(home_dir, "home_dir")]
        group_set = self._resolve_groups(groups, has_sudo)
        if group_set:
            opts += ["-G", ",".join(group_set)]

        rc, _out, stderr = await self.run(
            f"useradd {' '.join(opts)} {login}", sudo=True,
        )
        if rc != 0:
            raise SshError(
                error_code="SSH_USERADD_FAILED",
                host=self.host,
                cmd_sanitized=f"useradd <{login}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=f"useradd exit code {rc}",
            )
        if new_password is not None:
            await self.set_password(login, new_password)

    async def modify_user(
        self,
        login: str,
        *,
        groups: list[str] | None = None,
        has_sudo: bool = False,
        shell: str | None = None,
    ) -> None:
        """Синхронизировать атрибуты пользователя через `usermod`.

        Меняет login shell (`-s`) и состав дополнительных групп (`-G`, с
        перезаписью — флаг без `-a`, чтобы убрать выпавшие из аккаунта группы).
        Пароль здесь не трогаем — для пароля есть `set_password`. Если нечего
        менять (нет ни shell, ни групп, ни sudo) — no-op.
        """
        self._validate_login(login)
        opts: list[str] = []
        if shell is not None:
            opts += ["-s", self._safe_path(shell, "shell")]
        group_set = self._resolve_groups(groups, has_sudo)
        if group_set:
            opts += ["-G", ",".join(group_set)]
        if not opts:
            return
        rc, _out, stderr = await self.run(
            f"usermod {' '.join(opts)} {login}", sudo=True,
        )
        if rc != 0:
            raise SshError(
                error_code="SSH_USERMOD_FAILED",
                host=self.host,
                cmd_sanitized=f"usermod <{login}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=f"usermod exit code {rc}",
            )

    async def delete_user(self, login: str, *, remove_home: bool = False) -> None:
        """Удалить OS-пользователя через `userdel`.

        Idempotent: если пользователя нет — выходим без ошибки (deprovision
        повторяемо). `remove_home=True` добавляет `--remove` (снести home +
        mail spool). userdel может вернуть rc=6 «user does not exist» при гонке
        — трактуем как успех.
        """
        self._validate_login(login)
        if not await self.user_exists(login):
            return
        flag = "--remove " if remove_home else ""
        rc, _out, stderr = await self.run(
            f"userdel {flag}{login}", sudo=True,
        )
        # rc=6 — «user does not exist», для idempotency это успех.
        if rc not in (0, 6):
            raise SshError(
                error_code="SSH_USERDEL_FAILED",
                host=self.host,
                cmd_sanitized=f"userdel <{login}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=f"userdel exit code {rc}",
            )

    def _validate_login(self, login: str) -> None:
        """Отбить login с символами вне POSIX-набора до подстановки в команду."""
        if not _LOGIN_RE.match(login):
            raise SshError(
                error_code="SSH_INVALID_LOGIN",
                host=self.host,
                cmd_sanitized="useradd/usermod/userdel",
                message=f"login {login!r} contains disallowed characters",
            )

    def _safe_path(self, value: str, field: str) -> str:
        """Провалидировать путь/shell перед подстановкой в команду.

        Принимаем только безопасный набор (буквы/цифры/`/._-`), без пробелов
        и shell-метасимволов — это аргумент команды, не stdin.
        """
        if not _PATH_RE.match(value):
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized=f"useradd/usermod {field}",
                message=f"{field} {value!r} contains disallowed characters",
            )
        return value

    def _resolve_groups(
        self, groups: list[str] | None, has_sudo: bool,
    ) -> list[str]:
        """Собрать и провалидировать набор групп, доклеив sudo при `has_sudo`.

        Имена групп — тот же POSIX-набор, что и login. `has_sudo` добавляет
        `sudo` (если её ещё нет). Порядок стабильный, дубли убираем.
        """
        result: list[str] = []
        for g in groups or []:
            if not _GROUP_RE.match(g):
                raise SshError(
                    error_code="SSH_INVALID_ARG",
                    host=self.host,
                    cmd_sanitized="useradd/usermod -G",
                    message=f"group {g!r} contains disallowed characters",
                )
            if g not in result:
                result.append(g)
        if has_sudo and "sudo" not in result:
            result.append("sudo")
        return result

    # ── Inventory: structured facts ──────────────────────────────────────

    async def get_inventory(self) -> dict:
        """Собрать структурированный inventory сервера.

        Команды:

        * `hostname` — короткое имя ноды;
        * `uname -a` — kernel + arch;
        * `lscpu -J` — JSON cpu (модель/ядра/сокеты);
        * `lsblk -J -o NAME,SIZE,TYPE,MODEL,SERIAL` — диски;
        * `cat /etc/os-release` — KEY=VALUE с дистрибутивом;
        * `lspci -mm` — PCI-устройства (одной строкой `class "vendor"
          "device" ...`).

        Возврат — dict с ключами `hostname`, `kernel`, `cpu`, `disks`,
        `os`, `pci`. Каждый блок может содержать `error` с описанием,
        если команда вернула non-zero — частичный inventory лучше,
        чем полный фейл одной команды.
        """
        facts: dict = {}

        # 1. hostname — самая дешёвая sanity-check команда, если она
        # упадёт — остальные тоже упадут.
        facts["hostname"] = await self._capture_text("hostname")
        facts["kernel"] = await self._capture_text("uname -a")

        # 2. lscpu -J → JSON.
        facts["cpu"] = await self._capture_json("lscpu -J")

        # 3. lsblk -J → JSON.
        facts["disks"] = await self._capture_json(
            "lsblk -J -o NAME,SIZE,TYPE,MODEL,SERIAL"
        )

        # 4. /etc/os-release — KEY=VALUE (часть в кавычках).
        os_release_raw = await self._capture_text("cat /etc/os-release")
        facts["os"] = _parse_os_release(os_release_raw.get("stdout", "")) if isinstance(os_release_raw, dict) else {}
        if isinstance(os_release_raw, dict) and "error" in os_release_raw:
            facts["os"]["error"] = os_release_raw["error"]

        # 5. lspci -mm — построчно. Не парсим в class/vendor/device dict
        # (нужно дополнительно lspci -nn для PCI ID) — отдаём raw lines.
        lspci_raw = await self._capture_text("lspci -mm")
        if isinstance(lspci_raw, dict):
            lines = [ln for ln in lspci_raw.get("stdout", "").splitlines() if ln.strip()]
            facts["pci"] = {"devices": lines}
            if "error" in lspci_raw:
                facts["pci"]["error"] = lspci_raw["error"]

        return facts

    # ── User inventory: getent passwd / group / sudoers ─────────────────

    async def get_os_users(self) -> dict:
        """Собрать список реальных OS-пользователей сервера.

        Команды:

        * `getent passwd` — все пользователи (`login:x:uid:gid:gecos:home:shell`);
        * `getent group` — группы для определения sudo-членства;
        * `getent /etc/login.defs UID_MIN` через `cat` — порог системных UID;
        * `sudo -l -U <login>` тут НЕ делаем (дорого и требует root): sudo
          определяем по членству в `sudo`/`wheel`/`admin`-группах.

        Возврат — dict с ключами `passwd`, `group`, `login_defs`. Каждый блок —
        результат `_capture_text` (`{stdout, returncode}` либо `{error}`).
        Парсинг и UID-фильтр — на стороне `services/ssh_client.py`.
        """
        facts: dict = {}
        facts["passwd"] = await self._capture_text("getent passwd")
        facts["group"] = await self._capture_text("getent group")
        # login.defs читаем целиком — UID_MIN/UID_MAX парсятся на стороне
        # facts-маппера. Если файла нет — fallback на дефолтный UID_MIN.
        facts["login_defs"] = await self._capture_text("cat /etc/login.defs")
        return facts

    async def _capture_text(self, command: str) -> dict:
        """Запустить команду, вернуть `{stdout, stderr, returncode}` либо
        `{error: ..., returncode}` при non-zero. Не raise'ит — caller
        видит частичный inventory.
        """
        try:
            rc, out, err = await self.run(command)
        except SshError as exc:
            return {"error": f"{exc.error_code}: {exc.message}", "returncode": None}
        if rc != 0:
            return {"error": err.strip() or f"exit code {rc}", "returncode": rc, "stdout": out}
        return {"stdout": out.strip(), "stderr": err.strip(), "returncode": rc}

    async def _capture_json(self, command: str) -> dict:
        """То же, что _capture_text, но `stdout` парсится в JSON.

        Если json-parse фейлит — возвращаем `{error, raw_stdout}` чтобы
        caller мог увидеть, что пришло.
        """
        text = await self._capture_text(command)
        if "error" in text:
            return text
        raw = text.get("stdout", "")
        try:
            return {"data": json.loads(raw)}
        except (json.JSONDecodeError, ValueError) as exc:
            return {"error": f"invalid JSON: {exc}", "raw_stdout": raw[:512]}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sanitize_cmd(command: str) -> str:
    """Удалить из command'ы потенциальные password-substring'и.

    Базовая защита для exception-args. `chpasswd` сам по себе не несёт
    пароля в command-line (он на stdin), но защита эшелонированная:
    sudo может прокинуть `-p`, и если caller когда-нибудь начнёт писать
    `echo "$user:$pwd" | chpasswd`, регулярка отсечёт.
    """
    # `-p <something>` / `-P <something>` / `--password=...`
    sanitized = re.sub(r"--password=\S+", "--password=<PASSWORD>", command)
    sanitized = re.sub(r"(?<![A-Za-z0-9_])-[Pp]\s+\S+", "-P <PASSWORD>", sanitized)
    # Inline `echo "user:secret"` style.
    sanitized = re.sub(
        r'(echo\s+["\'])([A-Za-z0-9_.\-]+):[^"\']+(["\'])',
        r"\1\2:<PASSWORD>\3",
        sanitized,
    )
    return sanitized


def _scrub_password_echo(stderr: str, new_password: str) -> str:
    """Удалить из stderr точное совпадение `new_password`.

    Эшелонированная защита: redact_error_message не знает значение
    пароля (regex'ы матчат структуру). Здесь, имея на руках новый
    пароль, прибиваем его substring'ом до того, как stderr поедет в
    audit-логи.
    """
    if not new_password:
        return stderr
    return stderr.replace(new_password, "<PASSWORD>")


def _parse_os_release(text: str) -> dict:
    """Распарсить /etc/os-release в dict.

    Формат:

        NAME="Astra Linux SE"
        VERSION="1.7"
        ID=astra
        VERSION_ID="1.7"

    Возвращает все KEY=VALUE с убранными кавычками. Не падает на
    кривых строках — просто пропускает.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out
