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
* host-key НЕ проверяется (`known_hosts=None` для asyncssh — принять
  любой host-key). Флот тестовых серверов регулярно переустанавливается,
  host-key меняется при каждой переустановке, поэтому known_hosts /
  strict-проверка непрактичны: их пришлось бы инвалидировать после каждого
  reimage. Это осознанное ослабление — management-сеть считается доверенной.
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

# Whitelist префиксов SSH-public-key, которые принимаем для записи в
# authorized_keys. Включает классические `ssh-rsa`/`ssh-ed25519`/`ssh-dss`,
# elliptic-curve ECDSA трёх размеров и FIDO/U2F (`sk-*`). Trailing space — часть
# матчинга через `startswith`: гарантирует, что после prefix'а есть разделитель
# и мы не примем строку вида `ssh-rsasomething`.
_VALID_SSH_KEY_PREFIXES = (
    "ssh-rsa ",
    "ssh-ed25519 ",
    "ssh-dss ",
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ssh-ed25519@openssh.com ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
)

# Home-каталоги, в которые писать authorized_keys запрещено. `/` — обычный
# случай «getent не нашёл user'а» (home-поле пустое). Остальные — типичные
# системные псевдо-учётки (`nobody`, `daemon`, заблокированные сервисные
# аккаунты): если кто-то ошибочно подставит их login'ом, ключ ушёл бы в
# директорию вроде `/var/empty/.ssh/authorized_keys`, чего быть не должно.
# Guard сидит и в Python (на стороне _install_authorized_key), и в bash
# (на стороне remote-команды, после getent passwd) — defence-in-depth.
_FORBIDDEN_HOMES = frozenset({
    "/",
    "/dev",
    "/var/empty",
    "/usr/sbin/nologin",
    "/sbin/nologin",
    "/bin/false",
})


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
        timeout: float = 30.0,
        client_keys: list[str] | None = None,
    ) -> None:
        self.host = host
        self.username = username
        self._password = password
        self.port = port
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

        host-key не проверяется: `known_hosts=None` для asyncssh означает
        «принять любой host-key». Флот серверов часто переустанавливается,
        host-key при этом меняется, поэтому known_hosts-проверка непрактична.
        """
        if self._conn is not None:
            return

        try:
            self._conn = await asyncssh.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self._password,
                client_keys=self._client_keys,
                # known_hosts=None = «accept-any»: флот серверов часто
                # переустанавливается, host-key меняется — known_hosts-
                # проверка непрактична. Каналом доверия выступает password/key.
                known_hosts=None,
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
            # должен, но не доверяем). Маскируем оба: и пароль, и
            # `login:` prefix из payload `echo "user:pass" | chpasswd`.
            stderr_clean = _scrub_password_echo(stderr, new_password, login=login)
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
        public_key: str | None = None,
        force_replace: bool = False,
    ) -> None:
        """Завести OS-пользователя через `useradd` + опционально задать пароль.

        Idempotent: если пользователь уже существует — выходим без ошибки
        (provision повторяемо). Иначе собираем `useradd` с `-m` (создать home),
        `-s <shell>`, `-d <home>`, `-G <groups>` (sudo-группа доклеивается при
        `has_sudo`). После create'а, если задан `new_password`, ставим его
        через `chpasswd` (тот же путь, что `set_password`).

        Если задан `public_key` — пишем его в `~/.ssh/authorized_keys` юзера.
        `force_replace=True` затирает файл целиком (re-provision после
        переустановки ОС), иначе ключ добавляется idempotent'но (`grep -qxF`).

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
            if public_key is not None:
                await self._write_authorized_key(
                    login, public_key,
                    force_replace=force_replace,
                    target_home=home_dir,
                )
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
        if public_key is not None:
            await self._write_authorized_key(
                login, public_key,
                force_replace=force_replace,
                target_home=home_dir,
            )

    async def _write_authorized_key(
        self, login: str, public_key: str, *, force_replace: bool,
        target_home: str | None = None,
    ) -> None:
        """Записать `public_key` в `~/.ssh/authorized_keys` пользователя `login`.

        `force_replace=True` создаёт `authorized_keys` с нуля одним этим
        ключом (re-provision после переустановки ОС: старые записи теряют
        смысл). `force_replace=False` — идемпотентно дописывает ключ, если
        точного совпадения строки не нашлось через `grep -qxF`.

        `target_home` — если caller уже знает home аккаунта (пришёл через
        provision payload), Python-guard отобьёт системные пути из
        `_FORBIDDEN_HOMES` до отправки команды на хост. На bootstrap'е
        home заранее неизвестен — оставляем `None` и полагаемся на bash-guard
        после `getent passwd`.
        """
        self._validate_login(login)
        await self._install_authorized_key(
            target_user=login,
            public_key=public_key,
            truncate=force_replace,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
            target_home=target_home,
        )

    async def _install_authorized_key(
        self,
        *,
        target_user: str,
        public_key: str,
        truncate: bool,
        error_code: str,
        target_home: str | None = None,
    ) -> None:
        """Общая реализация записи ключа в `~/<user>/.ssh/authorized_keys`.

        Используется и обычным `_write_authorized_key` для server_account,
        и `bootstrap_management_user` для управляющего пользователя. На
        подходе третий call-site — ротация ключа управляющего пользователя.

        `target_user` обязан быть уже провалидирован caller'ом
        (`_validate_login` или эквивалент): подставляется в shell-команду,
        не через stdin. На всякий случай дублируем валидацию здесь —
        defense-in-depth: если в будущем появится call-site, забывший
        вызвать `_validate_login`, мы всё равно отобьём injection до того,
        как login попадёт в `getent passwd <user>` shell-команды.

        Валидирует ключ (single-line, известный prefix), собирает home
        через `getent passwd`, кладёт ключ на stdin (`$(cat)`), правит
        права 700/600 и chown. На `truncate=True` файл перезаписывается
        одним ключом; на `truncate=False` — идемпотентный append через
        `grep -qxF`.
        """
        self._validate_login(target_user)
        cmd_label = f"prepare authorized_keys <{target_user}>"
        if not public_key or not public_key.strip():
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized=cmd_label,
                message="public key is empty",
            )
        key_line = public_key.strip()
        # Single-line guard — вторая линия защиты от command-injection в чужие
        # ключи: `\n`/`$()`/backtick/`;` в самом ключе остаются на stdin (через
        # `key=$(cat)`), но если ключ когда-нибудь начнёт подставляться в
        # shell-строку, многострочный или с метасимволами он сломал бы парсинг.
        # Здесь же отсекаем CR/LF, чтобы invariant держался независимо от
        # будущих изменений транспорта.
        if "\n" in key_line or "\r" in key_line:
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized=cmd_label,
                message="public key must be a single line",
            )
        if not key_line.startswith(_VALID_SSH_KEY_PREFIXES):
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized=cmd_label,
                message="public key has unsupported algorithm prefix",
            )
        # Защита от case'а «caller передал системного пользователя» (nobody,
        # daemon, заблокированные сервисные аккаунты). Если caller знает
        # home заранее — отсекаем по списку до отправки команды на хост.
        # Парный bash-guard ниже отлавливает тот же класс ошибок, если
        # home резолвится только удалённо через getent passwd.
        if target_home is not None and target_home in _FORBIDDEN_HOMES:
            raise SshError(
                error_code="SSH_INVALID_HOME",
                host=self.host,
                cmd_sanitized=cmd_label,
                message=(
                    f"refusing to write authorized_keys into system home "
                    f"{target_home!r} for user {target_user!r}"
                ),
            )
        if truncate:
            write_cmd = (
                'printf "%s\\n" "$key" > "$home/.ssh/authorized_keys"'
            )
        else:
            write_cmd = (
                'touch "$home/.ssh/authorized_keys"; '
                'grep -qxF "$key" "$home/.ssh/authorized_keys" || '
                'printf "%s\\n" "$key" >> "$home/.ssh/authorized_keys"'
            )
        # `getent passwd <user>` — NSS-aware: проходит и через local
        # `/etc/passwd`, и через LDAP/SSSD/NIS, если они подключены в
        # `/etc/nsswitch.conf`. Прямое чтение `/etc/passwd` в стендах с
        # LDAP-учётками вернуло бы пустую строку → шаг чтения home упал бы
        # на работающем по факту пользователе. `cut -d: -f6` достаёт
        # шестое поле (home) из passwd-формата.
        # getent возвращает пустую строку, если пользователь не существует
        # (удалён между provision'ом и установкой ключа, либо вообще не
        # создан). Без guard'а home="" приводил бы к `mkdir -p /.ssh`
        # под sudo и порче корневой ФС. Явный exit 1 с сообщением в stderr
        # ловится caller'ом как обычный SSH_*_FAILED.
        # Дополнительно отбиваем home-каталоги типичных системных учёток
        # (`/dev`, `/var/empty`, заблокированные shell'ы `/usr/sbin/nologin`,
        # `/sbin/nologin`, `/bin/false`) — если кто-то по ошибке протащит
        # такой login через провижн server_account, ключ не уляжется в
        # неожиданном месте. Список синхронизирован с `_FORBIDDEN_HOMES`.
        bash_cmd = (
            f"bash -c 'set -e; "
            f"home=$(getent passwd {target_user} | cut -d: -f6); "
            'case "$home" in '
            '""|"/"|"/dev"|"/var/empty"|"/usr/sbin/nologin"|"/sbin/nologin"|"/bin/false") '
            f'echo "user {target_user} not found or has invalid home" >&2; '
            'exit 1;; '
            'esac; '
            'mkdir -p "$home/.ssh"; '
            "key=$(cat); "
            f"{write_cmd}; "
            f'chown -R {target_user}: "$home/.ssh"; '
            'chmod 700 "$home/.ssh"; '
            'chmod 600 "$home/.ssh/authorized_keys"\''
        )
        # Sanity-guard: ни один из подставляемых аргументов (target_user через
        # `_validate_login`, write_cmd литералом) не должен внести `\n` в
        # bash-строку. Без этого многострочная команда могла бы попасть в
        # asyncssh.run и быть интерпретирована как несколько отдельных
        # statement'ов. Защита эшелонированная — основной фильтр выше
        # (`_validate_login`, key newline guard), но invariant полезно
        # держать ближе к точке использования.
        assert "\n" not in bash_cmd and "\r" not in bash_cmd, (
            "authorized_keys command must be single-line"
        )
        rc, _out, stderr = await self.run(
            bash_cmd,
            sudo=True,
            stdin_payload=f"{key_line}\n",
        )
        if rc != 0:
            raise SshError(
                error_code=error_code,
                host=self.host,
                cmd_sanitized=cmd_label,
                returncode=rc,
                stderr=stderr.strip(),
                message=f"authorized_keys setup exit code {rc}",
            )

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

    async def bootstrap_management_user(
        self, management_user: str, public_key: str,
    ) -> None:
        """Завести управляющего пользователя DBOS и положить ему публичный ключ.

        Бутстрап-онбординг (`server.prepare`): сессия идёт под bootstrap-кредами
        (password-auth), под которыми мы заходим на ещё не управляемый сервер.
        Здесь мы:

        0. Pre-check: `getent passwd <user>` + `id -nG <user>`. Если юзер уже
           существует и состоит в `sudo` или `wheel` — useradd/usermod
           пропускаются целиком, идём сразу к sudoers + authorized_keys.
           Покрывает retry-сценарий «предыдущий прогон упал после
           useradd, но до sudoers» — повторно `usermod -G` не зовём
           (он перепишет group-list, NSS-кэш + sssd иногда показывают
           неполный список — потеряем существующее членство).
        1. `useradd -m -s /bin/bash -G <sudo-group> <management_user>` (idempotent —
           уже существующий пользователь синхронизируется как usermod,
           пароль не трогаем, ключ ниже всё равно доложим). Sudo-группа
           подбирается по дистрибутиву: `sudo` на Debian/Ubuntu/Astra,
           `wheel` на RHEL/Alpine — пробуем sudo первым, при provision-fail
           откатываемся на wheel. Если pre-check показал нужную группу,
           этот шаг **skip'ается** целиком.
        2. пишем `/etc/sudoers.d/<management_user>-management` с правилом
           `NOPASSWD: ALL` — на управляющей сессии пароля нет (заходим по
           ключу), поэтому sudo обязан работать без него;
        3. создаём `~/.ssh` с правами 700 и `authorized_keys` 600;
        4. дописываем `public_key` в authorized_keys, если его там ещё нет.

        Повторный prepare не падает: pre-check короткой дорогой обходит
        шаг 1, иначе useradd на existing → usermod, sudoers-файл
        перезаписывается, а ключ добавляется только при отсутствии
        (grep по точному совпадению строки).

        Пароль управляющему пользователю не ставим — управление дальше идёт по
        ключу. `public_key` — аргумент для безопасной записи через here-doc на
        stdin (не подставляется в командную строку, чтобы спецсимволы ключа /
        комментария не ломали shell).
        """
        self._validate_login(management_user)
        # Pre-валидируем ключ ДО useradd/sudoers (битый ключ — фейл setup'а
        # без побочных эффектов на /etc). Сам install ниже повторит проверку
        # в `_install_authorized_key` — это OK, regex дешёвый, дубль не мешает.
        if not public_key or not public_key.strip():
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized="prepare authorized_keys",
                message="management public key is empty",
            )
        key_line = public_key.strip()
        if "\n" in key_line or "\r" in key_line:
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized="prepare authorized_keys",
                message="management public key must be a single line",
            )
        if not key_line.startswith(_VALID_SSH_KEY_PREFIXES):
            raise SshError(
                error_code="SSH_INVALID_ARG",
                host=self.host,
                cmd_sanitized="prepare authorized_keys",
                message="management public key has unsupported algorithm prefix",
            )

        # 1. Управляющий пользователь — заводим idempotent'но, с sudo-группой.
        # На Debian/Ubuntu/Astra sudoer-группа — `sudo`, на RHEL/Alpine — `wheel`.
        # Пробуем `sudo`, при отказе useradd/usermod (`-G` ругается на
        # несуществующую группу) — `wheel`. NOPASSWD-правило ниже всё равно
        # перекрывает sudoers — членство в группе нужно для дефолтной policy
        # на тех дистрибутивах, где `/etc/sudoers.d/*` подгружается ленивее
        # системного `/etc/sudoers`.
        #
        # При повторном bootstrap'е (предыдущий прогон упал между useradd и
        # sudoers/auth_keys) пользователь уже существует и может уже состоять
        # в `sudo` или `wheel` — проверяем перед `usermod`, чтобы не дёргать
        # ненужную команду и не наступить на edge-case, когда usermod -G
        # переписывает текущую групп-листу при `id`-показании совпадающего
        # состояния (NSS-кэш, sssd, ldap-членство).
        last_exc: SshError | None = None
        provisioned = False
        existing_groups: set[str] = set()
        if await self.user_exists(management_user):
            existing_groups = await self._sudo_group_membership(management_user)
            if existing_groups & {"sudo", "wheel"}:
                logger.info(
                    "bootstrap: user %r already in sudo-group %s on %s, skipping useradd",
                    management_user, sorted(existing_groups), self.host,
                )
                provisioned = True

        if not provisioned:
            for sudo_group in ("sudo", "wheel"):
                try:
                    await self.create_user(
                        management_user,
                        groups=[sudo_group],
                        has_sudo=False,
                        shell="/bin/bash",
                    )
                except SshError as exc:
                    if exc.error_code not in ("SSH_USERADD_FAILED", "SSH_USERMOD_FAILED"):
                        raise
                    last_exc = exc
                    logger.info(
                        "bootstrap: sudo-group %r not available on %s, retrying",
                        sudo_group, self.host,
                    )
                    # Между неудачной попыткой и retry проверяем, не оказался
                    # ли пользователь уже в нужной группе — useradd мог успеть
                    # создать аккаунт до того как `-G` упал, или партнёрский
                    # процесс (puppet/ansible) подсадил его параллельно.
                    if await self.user_exists(management_user):
                        existing_groups = await self._sudo_group_membership(management_user)
                        if existing_groups & {"sudo", "wheel"}:
                            logger.info(
                                "bootstrap: user %r now in sudo-group %s on %s after partial failure",
                                management_user, sorted(existing_groups), self.host,
                            )
                            provisioned = True
                            break
                    continue
                provisioned = True
                break
        if not provisioned and last_exc is not None:
            raise last_exc

        # 2. NOPASSWD-правило. Управляющие сессии заходят по ключу без пароля,
        # а группа `sudo` по умолчанию пароль требует — без этого правила любая
        # последующая sudo-команда (useradd/usermod/userdel/chpasswd) упала бы.
        # Правило идёт на stdin `tee` (не в командную строку), сначала во
        # временный файл, проверяется `visudo -cf` и только при валидности
        # перемещается на место — битый sudoers не оставляем.
        #
        # `management_user` подставляется в shell-команду (sudoers_path) и в
        # sudoers-строку напрямую. Безопасно ТОЛЬКО потому, что
        # `_validate_login` отбивает всё, кроме `[A-Za-z0-9._-]` (см. валидацию
        # в начале метода). Ослаблять regex без перевода путей на shlex.quote
        # — мгновенный command-injection.
        sudoers_path = f"/etc/sudoers.d/{management_user}-management"
        sudoers_line = f"{management_user} ALL=(ALL) NOPASSWD: ALL"
        rc, _out, stderr = await self.run(
            "bash -c 'set -e; "
            'tmp=$(mktemp); cat > "$tmp"; '
            'chmod 440 "$tmp"; '
            'visudo -cf "$tmp"; '
            f'mv "$tmp" {sudoers_path}; '
            f'chmod 440 {sudoers_path}\'',
            sudo=True,
            stdin_payload=f"{sudoers_line}\n",
        )
        if rc != 0:
            raise SshError(
                error_code="SSH_PREPARE_FAILED",
                host=self.host,
                cmd_sanitized=f"prepare sudoers <{management_user}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=f"sudoers setup exit code {rc}",
            )

        # 3-4. ~/.ssh + authorized_keys и дозапись ключа. Повторный prepare
        # должен быть идемпотентным — `truncate=False` дописывает ключ только
        # при отсутствии точного совпадения (grep -qxF). Сам `_install_*` на
        # ошибке поднимает SshError с `error_code="SSH_PREPARE_FAILED"` —
        # bootstrap'у удобнее, чтобы оператор отличал сетап управляющего
        # пользователя от обычного account-flow'а.
        await self._install_authorized_key(
            target_user=management_user,
            public_key=public_key,
            truncate=False,
            error_code="SSH_PREPARE_FAILED",
        )

    async def _sudo_group_membership(self, login: str) -> set[str]:
        """Вернуть подмножество `{sudo, wheel}`, в которых состоит login.

        Используется bootstrap'ом для пропуска `useradd`/`usermod`, если
        предыдущий прогон уже успел подсадить пользователя в нужную группу
        (полу-успешный run). `id -nG` дешёвый, без sudo.
        """
        self._validate_login(login)
        rc, out, _err = await self.run(f"id -nG {login}")
        if rc != 0:
            return set()
        groups = set(out.split())
        return groups & {"sudo", "wheel"}

    def _validate_login(self, login: str) -> None:
        """Отбить login с символами вне POSIX-набора до подстановки в команду.

        Контракт: каждый call-site, который подставляет `login` в shell-строку
        (useradd / usermod / userdel / getent / id -nG / chpasswd payload /
        authorized_keys mkdir / sudoers-path) ОБЯЗАН позвать `_validate_login`
        ДО конструкции команды. Без `shlex.quote` безопасность держится
        исключительно на `_LOGIN_RE` charset ([A-Za-z0-9._-]) — добавление
        любого нового метода с прямой подстановкой login без предварительного
        `_validate_login` мгновенно открывает command-injection. Если ослабить
        `_LOGIN_RE`, потребуется переход на `shlex.quote` во всех call-site'ах.
        """
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


def _scrub_password_echo(
    stderr: str, new_password: str, login: str | None = None,
) -> str:
    """Удалить из stderr точное совпадение `new_password` (и опционально login'а).

    Эшелонированная защита: `redact_error_message` не знает значение
    пароля (regex'ы матчат структуру). Здесь, имея на руках новый
    пароль, прибиваем его substring'ом до того, как stderr поедет в
    audit-логи.

    Если передан `login`, дополнительно вырезаем `login:` (точная пара
    «логин + двоеточие», как в chpasswd-payload `echo "user:pass" |
    chpasswd`). Маскируем именно префикс с двоеточием, а не голый login:
    голый login встречается в десятке других контекстов (sudo prompt,
    `bash: <login>: command not found`), затирая его везде, потеряли бы
    debug-полезную информацию.
    """
    if new_password:
        stderr = stderr.replace(new_password, "<PASSWORD>")
    if login:
        stderr = stderr.replace(f"{login}:", "<LOGIN>:")
    return stderr


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
