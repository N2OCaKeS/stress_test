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

import asyncio
import logging
import re
from dataclasses import dataclass, field

import asyncssh

from src.clients import _command_builders as cmd_builders

logger = logging.getLogger(__name__)


# ── Per-host connection backpressure ──────────────────────────────────────────
#
# Семафор на (host, port): ограничивает число одновременных SSH-коннектов к
# одному target-хосту. Без него burst задач на один сервер (массовый provision +
# inventory одного бокса) открывает N параллельных сессий, упирается в sshd
# `MaxStartups` (random-drop после порога неаутентифицированных коннектов), и
# worker отвечает на drop'ы ретраями — амплификация. Семафоры держатся
# per-process в module-level реестре; к разным хостам сессии остаются
# параллельны. На multi-replica это per-replica backpressure (общего лимита
# через Redis тут нет — sshd MaxStartups сам по себе per-connection, и реплик
# немного).
_HOST_SEMAPHORES: dict[tuple[str, int], asyncio.Semaphore] = {}
_HOST_SEMAPHORE_LIMITS: dict[tuple[str, int], int] = {}
_HOST_SEMAPHORES_LOCK = asyncio.Lock()


def _default_max_sessions_per_host() -> int:
    """Лимит коннектов к одному хосту из настроек воркера.

    Читается лениво (не на import'е), чтобы транспорт оставался отвязан от
    config'а в тестах, которые конструируют `SshClient` напрямую. Любая
    ошибка резолва настроек (например, незаполненные required-поля в голом
    unit-окружении) — мягкий fallback на разумный дефолт, чтобы backpressure
    никогда не валил саму SSH-операцию.
    """
    try:
        from src.core.config import get_settings

        return int(get_settings().ssh_max_sessions_per_host)
    except Exception:  # noqa: BLE001 — backpressure не должен ронять транспорт
        return 4


async def _acquire_host_slot(host: str, port: int, limit: int) -> asyncio.Semaphore:
    """Взять слот в per-host семафоре; вернуть сам семафор для последующего release.

    Реестр семафоров защищён `_HOST_SEMAPHORES_LOCK`, чтобы конкурентные
    коннекты к новому хосту не создали два разных семафора (race на
    `dict.setdefault` под await не спасает — создание объекта дешёвое, но
    инвариант «один семафор на (host, port)» важен). Если для хоста уже есть
    семафор с другим лимитом — оставляем существующий (лимит меняется только
    рестартом воркера; не пересоздаём на лету, чтобы не потерять уже занятые
    слоты).
    """
    key = (host, port)
    async with _HOST_SEMAPHORES_LOCK:
        sem = _HOST_SEMAPHORES.get(key)
        if sem is None:
            sem = asyncio.Semaphore(limit)
            _HOST_SEMAPHORES[key] = sem
            _HOST_SEMAPHORE_LIMITS[key] = limit
    await sem.acquire()
    return sem


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

    def __str__(self) -> str:
        # `message` несёт человекочитаемую причину (actionable hint вроде
        # «возможно, ОС переустановлена — нужен повторный prepare»). Раньше он
        # в строку не попадал, и оператор в task.last_error видел только сухое
        # `SSH_AUTH_FAILED: host=... rc=None cmd='' stderr=''`. Включаем его,
        # когда он задан, не светя при этом креды: ни password, ни new_password
        # в message не пишутся (см. контракт docstring'а класса).
        base = (
            f"{self.error_code}: host={self.host} rc={self.returncode} "
            f"cmd={self.cmd_sanitized!r} stderr={self.stderr!r}"
        )
        if self.message:
            return f"{base} message={self.message!r}"
        return base


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

# Режимы создания управляющей учётки. Зеркалят `ManagementMode` в
# server_service (`core/constants.py`): три уровня защищённости Astra Linux SE
# (Орёл/Воронеж/Смоленск, они же базовый/усиленный/максимальный) и общий
# fallback для не-Астры. Worker не импортирует server_service-enum через
# DB-границу, поэтому держим строки здесь; меняешь там — синхронизируй тут.
MODE_ASTRA_OREL = "astra_orel"
MODE_ASTRA_VORONEZH = "astra_voronezh"
MODE_ASTRA_SMOLENSK = "astra_smolensk"
MODE_OTHER_OS = "other_os"

# astra-modeswitch отдаёт уровень защищённости числом: 0 — Орёл (базовый),
# 1 — Воронеж (усиленный), 2 — Смоленск (максимальный). В современной Astra
# SE 1.7+ это не отдельные дистрибутивы, а режимы одной ОС.
_ASTRA_LEVEL_TO_MODE = {
    "0": MODE_ASTRA_OREL,
    "1": MODE_ASTRA_VORONEZH,
    "2": MODE_ASTRA_SMOLENSK,
}

# Маркер, которым помечаем строки authorized_keys, записанные нами. Кладём
# его в конец строки как часть key-comment'а: OpenSSH игнорирует всё после
# base64-payload'а до конца строки, поэтому маркер не ломает разбор ключа, но
# даёт нам способ найти и заменить именно «свой» ключ при ротации, не трогая
# строки, добавленные оператором руками. Токен намеренно узкий (буквы/цифры/
# дефис), без пробелов и shell-метасимволов — он подставляется в bash-фильтр.
_MANAGED_KEY_MARKER = "dbos-managed-key"

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
    # Debian `nobody`, `_apt` и часть служебных учёток держат
    # `/nonexistent` в поле home. Запись в `/nonexistent/.ssh/authorized_keys`
    # под sudo на ряде дистрибутивов реально создаст каталог в корне ФС —
    # бессмысленно и опасно.
    "/nonexistent",
    # `/run/sshd` — home `sshd`-демона (privsep'ный chroot); пользовательских
    # сессий быть не должно.
    "/run/sshd",
    "/usr/sbin/nologin",
    "/sbin/nologin",
    "/bin/false",
})


def _validate_ssh_public_key(
    public_key: str,
    *,
    host: str,
    cmd_label: str = "prepare authorized_keys",
) -> str:
    """Канонический валидатор SSH-public-key для authorized_keys.

    Зовётся `_install_authorized_key` (внутренняя установка ключа) и
    `bootstrap_management_user` (pre-чек до useradd/sudoers, чтобы не
    оставлять побочных эффектов на /etc при битом ключе).

    Возвращает stripped key-line при успехе; иначе `SshError(SSH_INVALID_ARG)`.

    Что проверяем:
      * непустой, после strip;
      * single-line (нет `\n` / `\r`) — защита от command-injection через
        многострочный «ключ», даже если транспорт когда-нибудь начнёт
        подставлять ключ в shell-строку;
      * known prefix из `_VALID_SSH_KEY_PREFIXES` — алгоритм должен быть в
        whitelist'е, trailing space гарантирует разделитель между алгоритмом
        и base64-payload'ом.
    """
    if not public_key or not public_key.strip():
        raise SshError(
            error_code="SSH_INVALID_ARG",
            host=host,
            cmd_sanitized=cmd_label,
            message="public key is empty",
        )
    key_line = public_key.strip()
    if "\n" in key_line or "\r" in key_line:
        raise SshError(
            error_code="SSH_INVALID_ARG",
            host=host,
            cmd_sanitized=cmd_label,
            message="public key must be a single line",
        )
    if not key_line.startswith(_VALID_SSH_KEY_PREFIXES):
        raise SshError(
            error_code="SSH_INVALID_ARG",
            host=host,
            cmd_sanitized=cmd_label,
            message="public key has unsupported algorithm prefix",
        )
    return key_line


def _coerce_private_key(material, host: str):
    """Привести приватный ключ к виду, пригодному для asyncssh `client_keys`.

    Per-server ключ приходит plaintext-строкой (PEM). asyncssh трактует голую
    строку в `client_keys` как путь к файлу, поэтому PEM импортируем явно. Путь
    к файлу и уже импортированный key-объект пропускаем как есть. Битый PEM →
    `SshError(SSH_MANAGEMENT_KEY_INVALID)`.
    """
    if isinstance(material, str) and "PRIVATE KEY" in material:
        try:
            return asyncssh.import_private_key(material)
        except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_INVALID",
                host=host,
                message=f"management private key import failed: {type(exc).__name__}",
            ) from exc
    return material


def parse_management_mode(probe_output: str) -> str:
    """Распарсить вывод probe-команды детекта редакции в `ManagementMode`-строку.

    Probe-команда (`SshClient.detect_management_mode`) печатает несколько
    помеченных строк, из которых нам важны две:

      * `ASTRA=<...>` — непустое значение означает «это Astra Linux»
        (нашли `/etc/astra_version`, `/etc/astra/build_version`,
        `/etc/astra-release` или `ID=astra` в os-release);
      * `LEVEL=<0|1|2>` — уровень защищённости (`astra-modeswitch get`,
        fallback `/etc/parsec/mswitch.conf`). 0 → Орёл, 1 → Воронеж,
        2 → Смоленск.

    Логика:
      * не Astra → `other_os`;
      * Astra с распознанным уровнем → соответствующий режим;
      * Astra без читаемого уровня (старый релиз / урезанный образ без
        modeswitch и mswitch.conf) → консервативный дефолт `astra_orel`
        (базовый), чтобы bootstrap не падал на отсутствии данных.

    Чистая функция — без SSH, удобно юнит-тестировать на строках.
    """
    is_astra = False
    level: str | None = None
    for raw_line in probe_output.splitlines():
        line = raw_line.strip()
        if line.startswith("ASTRA="):
            value = line[len("ASTRA="):].strip()
            if value:
                is_astra = True
        elif line.startswith("LEVEL="):
            value = line[len("LEVEL="):].strip()
            # Берём первый числовой символ — modeswitch иногда печатает
            # «0\n» или «Current mode: 0», mswitch.conf — «MODE=0».
            for ch in value:
                if ch in _ASTRA_LEVEL_TO_MODE:
                    level = ch
                    break
    if not is_astra:
        return MODE_OTHER_OS
    if level is not None:
        return _ASTRA_LEVEL_TO_MODE[level]
    return MODE_ASTRA_OREL


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
        max_sessions_per_host: int | None = None,
    ) -> None:
        self.host = host
        self.username = username
        self._password = password
        # Требует ли sudo пароль на этом хосте. None — ещё не проверяли; выясняем
        # лениво `sudo -n true` при первом sudo-вызове и кэшируем на коннект.
        # Нужен, чтобы не подмешивать SSH-пароль в stdin, когда учётка настроена
        # NOPASSWD (sudo -S не потребляет строку — иначе пароль утекает в payload
        # sudoers/chpasswd).
        self._sudo_needs_password: bool | None = None
        self.port = port
        self.timeout = timeout
        self._client_keys = client_keys
        self._conn: asyncssh.SSHClientConnection | None = None
        # Лимит per-host коннектов. None → берём из настроек воркера лениво на
        # connect'е (см. `_default_max_sessions_per_host`). Тесты могут передать
        # явный лимит, чтобы не тащить config.
        self._max_sessions_per_host = max_sessions_per_host
        # Семафор-слот, занятый этим коннектом; освобождается в close().
        self._host_slot: asyncio.Semaphore | None = None

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

        # Per-host backpressure: занимаем слот ДО открытия сокета. Сериализует
        # коннекты сверх лимита к одному (host, port); к разным хостам — нет.
        # Слот держится до close(); на любой ошибке коннекта освобождаем сразу,
        # чтобы упавшая попытка не «съедала» слот.
        limit = (
            self._max_sessions_per_host
            if self._max_sessions_per_host is not None
            else _default_max_sessions_per_host()
        )
        self._host_slot = await _acquire_host_slot(self.host, self.port, limit)

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
            self._release_host_slot()
            raise SshError(
                error_code="SSH_AUTH_FAILED",
                host=self.host,
                message=f"authentication failed: {type(exc).__name__}",
            ) from exc
        except (TimeoutError, asyncssh.ConnectionLost, OSError) as exc:
            self._release_host_slot()
            raise SshError(
                error_code="SSH_CONNECT_FAILED",
                host=self.host,
                message=f"connect failed: {type(exc).__name__}: {exc}",
            ) from exc
        except asyncssh.Error as exc:
            self._release_host_slot()
            raise SshError(
                error_code="SSH_CONNECT_FAILED",
                host=self.host,
                message=f"asyncssh error: {type(exc).__name__}",
            ) from exc
        except BaseException:
            # CancelledError / прочее во время handshake — слот тоже отпускаем,
            # иначе он утечёт и забэкпрешерит хост навсегда.
            self._release_host_slot()
            raise

    def _release_host_slot(self) -> None:
        """Отпустить занятый per-host слот. Идемпотентно."""
        if self._host_slot is not None:
            self._host_slot.release()
            self._host_slot = None

    async def close(self) -> None:
        """Закрыть SSH-соединение и отпустить per-host слот. Идемпотентно."""
        if self._conn is None:
            # Коннект не открыт (или уже закрыт), но слот мог остаться занятым,
            # если кто-то занял его и не дошёл до connect'а — подстраховка.
            self._release_host_slot()
            return
        try:
            self._conn.close()
            await self._conn.wait_closed()
        except Exception:  # noqa: BLE001
            # Closing failure — это уже посмертный шум, не критично.
            logger.debug("error during ssh close to %s", self.host, exc_info=True)
        finally:
            self._conn = None
            self._release_host_slot()

    # ── Interactive PTY: invoke_shell-style session ──────────────────────

    async def open_pty(
        self, *, term_type: str = "xterm-256color",
        term_size: tuple[int, int] = (80, 24),
        command: str | None = None,
    ):
        """Открыть интерактивную PTY-сессию поверх текущего соединения.

        Возвращает `asyncssh` process с allocated pty (stdin/stdout/stderr —
        bidirectional streams). Используется console-мостом: ввод
        клиента пишется в `process.stdin`, вывод читается из
        `process.stdout`. Сессия должна быть закрыта `process.close()` +
        `process.wait_closed()` (или через `aclose`), иначе SSH-канал
        останется открытым.

        `command=None` поднимает login-shell (invoke_shell — серверная консоль
        под аккаунтом). С `command` PTY выполняет именно эту команду через
        exec-запрос: текст команды НЕ попадает в терминал (клиент его не видит,
        в отличие от набора в интерактивном shell'е), поэтому здесь безопасно
        передавать вложенный `sshpass ... ssh` для консоли ВМ или
        `virsh console` для serial'а.

        `term_type`/`term_size` дают удалённому shell'у разумный TERM,
        чтобы интерактивные программы (`top`, `vi`) рендерились корректно.
        """
        if self._conn is None:
            raise SshError(
                error_code="SSH_NOT_CONNECTED",
                host=self.host,
                cmd_sanitized="open_pty",
                message="open_pty() called before connect()",
            )
        try:
            return await self._conn.create_process(
                *( (command,) if command is not None else () ),
                term_type=term_type,
                term_size=term_size,
                encoding=None,  # bytes in/out — мост не интерпретирует кодировку
            )
        except asyncssh.Error as exc:
            raise SshError(
                error_code="SSH_PTY_FAILED",
                host=self.host,
                cmd_sanitized="open_pty",
                message=f"failed to open pty: {type(exc).__name__}",
            ) from exc

    # ── Core: run a command ──────────────────────────────────────────────

    async def _sudo_requires_password(self) -> bool:
        """True, если sudo на этом хосте спрашивает пароль (не NOPASSWD).

        Проверяем `sudo -n true` один раз на коннект и кэшируем: `-n` не
        интерактивен, поэтому NOPASSWD-учётка вернёт rc 0, а требующая пароль —
        ненулевой код (sudo не станет ждать ввод). Результат кэшируется в
        `self._sudo_needs_password`.
        """
        if self._sudo_needs_password is None:
            if self._conn is None:
                return bool(self._password)
            probe = await self._conn.run("sudo -n true", check=False)
            self._sudo_needs_password = probe.exit_status != 0
        return self._sudo_needs_password

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
            # sudo password подаётся первой строкой stdin, payload (если есть) —
            # после, разделено LF: первая строка — пароль для sudo, вторая —
            # `login:newpwd` для chpasswd.
            #
            # Но подмешивать пароль можно ТОЛЬКО когда sudo реально его читает.
            # Два случая, когда `sudo -S` первую строку НЕ потребляет:
            #   * key-сессия без пароля (`self._password is None`);
            #   * учётка с NOPASSWD sudo — вход по паролю, но sudo пароля не
            #     спрашивает (частый кейс bootstrap-юзера на prepare).
            # В обоих случаях лидирующая строка ушла бы команде как payload:
            # пароль оказался бы строкой 1 sudoers-файла (syntax error) или
            # chpasswd прочитал бы пустую строку и упал. Поэтому подмешиваем
            # пароль только если он есть И sudo его действительно требует.
            if self._password and await self._sudo_requires_password():
                stdin_full = f"{self._password}\n" + (stdin_payload or "")
            else:
                stdin_full = stdin_payload

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
        if not _LOGIN_RE.fullmatch(login):
            raise SshError(
                error_code="SSH_INVALID_LOGIN",
                host=self.host,
                cmd_sanitized="chpasswd",
                message=f"login {login!r} contains characters disallowed for chpasswd",
            )

        # Пустой/None пароль — не дёргаем chpasswd: на payload'е `login:\n`
        # он падает с `missing new password`. Для passwordless-аккаунтов
        # (discovered без хранимого пароля) это не ошибка, а «пароль не
        # задаём» — тихо выходим. Контракт держим и здесь, на дне, чтобы
        # ни один call-site, забывший свой `if new_password:`, не наступил
        # на missing-new-password. Caller'ы, которым пустой пароль —
        # ошибка (ротация обязана выставить реальный секрет), отбивают его
        # сами до вызова.
        if not new_password:
            logger.debug(
                "set_password no-op for %r on %s: empty password",
                login, self.host,
            )
            return

        # Сам payload — `login:newpwd\n`. Не логируется, не попадает в
        # cmd_sanitized.
        command, payload = cmd_builders.build_set_password(
            login, new_password, flavor=cmd_builders.SERVER,
        )
        rc, stdout, stderr = await self.run(
            command,
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
        if not _LOGIN_RE.fullmatch(login):
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
        nopasswd_sudo: bool = False,
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

        `nopasswd_sudo=True` (server_service резолвит это заранее из per-department
        настройки `/settings/account-nopasswd-sudo`) вдобавок кладёт per-user
        NOPASSWD sudoers-правило (`install_account_sudoers`) — членство в
        группе `sudo` из `has_sudo` этот пароль не отменяет, отдельная запись
        нужна именно чтобы прицельно снять запрос пароля с одной учётки, не
        трогая остальных членов группы. `nopasswd_sudo=False` (дефолт, и весь
        существующий трафик, где отдел настройку не включал) — ни одной лишней
        SSH-команды: no-op, sudoers не трогаем вовсе.

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
                nopasswd_sudo=nopasswd_sudo,
            )
            # Пустой/None пароль = «не ставить пароль»: chpasswd на пустом
            # payload'е (`login:\n`) падает с 'missing new password'.
            # Discovered-аккаунты без пароля заводятся useradd + ключ.
            if new_password:
                await self.set_password(login, new_password)
            if public_key is not None:
                await self._write_authorized_key(
                    login, public_key,
                    force_replace=force_replace,
                    target_home=home_dir,
                )
            return

        safe_shell = self._safe_path(shell, "shell") if shell is not None else None
        safe_home = (
            self._safe_path(home_dir, "home_dir") if home_dir is not None else None
        )
        group_set = self._resolve_groups(groups, has_sudo)

        rc, _out, stderr = await self.run(
            cmd_builders.build_useradd(
                login, flavor=cmd_builders.SERVER,
                shell=safe_shell, home_dir=safe_home, groups=group_set,
            ),
            sudo=True,
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
        # См. existing-ветку выше: пустой/None пароль — не ставим (chpasswd на
        # пустом payload'е падает 'missing new password').
        if new_password:
            await self.set_password(login, new_password)
        if public_key is not None:
            await self._write_authorized_key(
                login, public_key,
                force_replace=force_replace,
                target_home=home_dir,
            )
        if nopasswd_sudo:
            await self.install_account_sudoers(login)

    async def _write_authorized_key(
        self, login: str, public_key: str, *, force_replace: bool,
        target_home: str | None = None,
    ) -> None:
        """Записать `public_key` в `~/.ssh/authorized_keys` пользователя `login`.

        Ключ помечается managed-маркером (`_MANAGED_KEY_MARKER`) в конце
        строки: при ротации мы заменяем именно прежнюю managed-строку, не
        трогая ключи, которые оператор добавил руками. Запись идемпотентна —
        повторный вызов с тем же ключом ничего не меняет.

        `force_replace=True` создаёт `authorized_keys` с нуля одним этим
        ключом (re-provision после переустановки ОС: всё прежнее содержимое,
        включая ручные ключи, теряет смысл — бокс переустановлен).
        `force_replace=False` — заменяет прежний managed-ключ на новый и
        оставляет остальные строки нетронутыми.

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
            managed=True,
        )

    async def _install_authorized_key(
        self,
        *,
        target_user: str,
        public_key: str,
        truncate: bool,
        error_code: str,
        target_home: str | None = None,
        managed: bool = False,
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
        права 700/600 и chown.

        Режимы записи:

          * `truncate=True` — файл перезаписывается одним ключом (re-provision
            после переустановки ОС, всё прежнее содержимое уже невалидно).
          * `truncate=False, managed=False` — идемпотентный append голого
            ключа через `grep -qxF` (bootstrap управляющего пользователя:
            маркер там не нужен, ключ всегда один и тот же).
          * `truncate=False, managed=True` — ключ записывается с
            managed-маркером в конце строки. Прежние строки с тем же маркером
            удаляются перед записью (ротация заменяет именно наш ключ), а
            строки без маркера — ручные ключи оператора — остаются нетронутыми.
            Если ровно эта строка уже есть, повторная запись её не дублирует.
        """
        self._validate_login(target_user)
        cmd_label = f"prepare authorized_keys <{target_user}>"
        # Single-line / known-prefix / non-empty — общий валидатор
        # `_validate_ssh_public_key`. Защита от command-injection в чужие
        # ключи: `\n`/`$()`/backtick/`;` в самом ключе остаются на stdin
        # (через `key=$(cat)`), но если ключ когда-нибудь начнёт подставляться
        # в shell-строку, многострочный или с метасимволами он сломал бы
        # парсинг. Здесь же отсекаем CR/LF, чтобы invariant держался
        # независимо от будущих изменений транспорта.
        key_line = _validate_ssh_public_key(
            public_key, host=self.host, cmd_label=cmd_label,
        )
        # В managed-режиме помечаем строку маркером, чтобы при ротации найти и
        # снести именно её. Маркер — отдельное слово в хвосте строки (часть
        # key-comment'а с точки зрения OpenSSH). Если ключ уже несёт маркер
        # (не должно происходить, ключ приходит из server_service без него),
        # второй раз не клеим.
        if managed and not truncate and _MANAGED_KEY_MARKER not in key_line:
            key_line = f"{key_line} {_MANAGED_KEY_MARKER}"
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
        # Режим записи для билдера. `managed` фильтрует прежние строки с нашим
        # маркером и делает idempotent append (ротация заменяет именно наш
        # ключ, ручные строки оператора не трогаются); `plain` — idempotent
        # append голого ключа (bootstrap управляющего пользователя, маркер там
        # не нужен); `truncate` — перезапись файла одним ключом.
        if truncate:
            write_mode = "truncate"
        elif managed:
            write_mode = "managed"
        else:
            write_mode = "plain"
        # Билдер собирает bash под sudo: `getent passwd <user>` (NSS-aware —
        # видит и local `/etc/passwd`, и LDAP/SSSD/NIS), `cut -d: -f6` достаёт
        # home; пустой home (юзера нет) и системные home'ы (`/dev`,
        # `/var/empty`, `/nonexistent`, `/run/sshd`, nologin-shell'ы) отбиваются
        # case-guard'ом — иначе `mkdir -p /.ssh` под sudo испортил бы корень ФС.
        # Case-список зашит в билдере и синхронизирован с `_FORBIDDEN_HOMES`.
        # Ключ приходит на stdin (`key=$(cat)`), в командную строку не попадает.
        bash_cmd = cmd_builders.build_authorized_keys(
            target_user, flavor=cmd_builders.SERVER,
            write_mode=write_mode, marker=_MANAGED_KEY_MARKER,
        )
        # Sanity-guard: ни один из подставляемых аргументов (target_user через
        # `_validate_login`, write-фрагмент литералом в билдере) не должен
        # внести `\n` в bash-строку. Без этого многострочная команда могла бы попасть в
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
        nopasswd_sudo: bool = False,
    ) -> None:
        """Синхронизировать атрибуты пользователя через `usermod`.

        Меняет login shell (`-s`) и состав дополнительных групп (`-G`, с
        перезаписью — флаг без `-a`, чтобы убрать выпавшие из аккаунта группы).
        Пароль здесь не трогаем — для пароля есть `set_password`. Если нечего
        менять (ни shell, ни групп, ни sudo) — no-op.

        `nopasswd_sudo=True` дополнительно кладёт per-user NOPASSWD sudoers-
        правило (`install_account_sudoers`), как и `create_user`.
        `nopasswd_sudo=False` (дефолт) sudoers вообще не трогает — самой
        настройки может не быть в игре для этого вызова, лишняя SSH-команда на
        каждый usermod (в т.ч. под капотом `bootstrap_management_user`, где
        `nopasswd_sudo` никогда не передаётся) была бы чистым накладным
        расходом. Снятие уже поставленного правила при выключении настройки —
        отдельный явный путь через `remove_account_sudoers`, не эта функция.
        """
        self._validate_login(login)
        safe_shell = self._safe_path(shell, "shell") if shell is not None else None
        group_set = self._resolve_groups(groups, has_sudo)
        command = cmd_builders.build_usermod(
            login, flavor=cmd_builders.SERVER,
            shell=safe_shell, groups=group_set,
        )
        # Нечего менять (ни shell, ни групп) — билдер вернул None, no-op.
        if command is not None:
            rc, _out, stderr = await self.run(command, sudo=True)
            if rc != 0:
                raise SshError(
                    error_code="SSH_USERMOD_FAILED",
                    host=self.host,
                    cmd_sanitized=f"usermod <{login}>",
                    returncode=rc,
                    stderr=stderr.strip(),
                    message=f"usermod exit code {rc}",
                )
        if nopasswd_sudo:
            await self.install_account_sudoers(login)

    async def install_account_sudoers(self, login: str) -> bool:
        """Положить per-user NOPASSWD sudoers-правило для одной учётки.

        Отдельный drop-in `/etc/sudoers.d/<login>-nopasswd` — НЕ трогает
        группу `sudo` целиком и НЕ эскалирует привилегии прочих её членов.
        По образцу `bootstrap_management_user`'овского шага 2: правило идёт на
        stdin, сначала во временный файл, `visudo -cf` валидирует его ДО
        перемещения на место (`build_sudoers_install`) — битый sudoers никогда
        не долетает до `/etc/sudoers.d/`.

        Fail-safe, не fail-open: если валидация не прошла (`visudo` вернул
        non-zero — испорченный login/окружение на боксе), НЕ поднимаем
        `SshError` — логируем предупреждение и возвращаем `False`. Аккаунт
        остаётся в обычной парольно-запрашивающей группе `sudo` (уже
        применённой `has_sudo`-веткой выше), провижн в целом не проваливается.
        """
        self._validate_login(login)
        sudoers_path = f"/etc/sudoers.d/{login}-nopasswd"
        sudoers_line = cmd_builders.build_account_sudoers_line(login)
        rc, _out, stderr = await self.run(
            cmd_builders.build_sudoers_install(sudoers_path),
            sudo=True,
            stdin_payload=f"{sudoers_line}\n",
        )
        if rc != 0:
            logger.warning(
                "nopasswd sudoers for %s on %s rejected by visudo (rc=%s): %s",
                login, self.host, rc, stderr.strip(),
            )
            return False
        return True

    async def remove_account_sudoers(self, login: str) -> None:
        """Снести `/etc/sudoers.d/<login>-nopasswd`, если он был поставлен.

        Idempotent (`rm -f`) — зовётся из `delete_user` только когда caller
        (server_service, через `nopasswd_sudo` в payload) подтвердил, что файл
        мог существовать. Не должен ронять caller'а: неудачный `rm` —
        предупреждение в лог, не исключение (осиротевший файл ссылается на
        снесённого пользователя и сам по себе привилегий не даёт).
        """
        self._validate_login(login)
        sudoers_path = f"/etc/sudoers.d/{login}-nopasswd"
        rc, _out, stderr = await self.run(f"rm -f {sudoers_path}", sudo=True)
        if rc != 0:
            logger.warning(
                "failed to remove nopasswd sudoers for %s on %s (rc=%s): %s",
                login, self.host, rc, stderr.strip(),
            )

    async def delete_user(
        self, login: str, *, remove_home: bool = False, nopasswd_sudo: bool = False,
    ) -> None:
        """Удалить OS-пользователя через `userdel`.

        Idempotent: если пользователя нет — не падаем на userdel'е (deprovision
        повторяемо). `remove_home=True` добавляет `--remove` (снести home +
        mail spool). userdel может вернуть rc=6 «user does not exist» при гонке
        — трактуем как успех.

        `nopasswd_sudo=True` — server_service подтверждает, что у аккаунта
        мог стоять per-user NOPASSWD sudoers (`has_sudo=True` и отдел на момент
        deprovision держал настройку включённой): подчищаем
        `/etc/sudoers.d/<login>-nopasswd`. `False` (дефолт — аккаунт никогда не
        был sudo, либо настройка отделу не касается) — sudoers не трогаем
        вовсе, лишней SSH-команды на каждый userdel нет.
        """
        self._validate_login(login)
        if not await self.user_exists(login):
            if nopasswd_sudo:
                await self.remove_account_sudoers(login)
            return
        rc, _out, stderr = await self.run(
            cmd_builders.build_userdel(
                login, flavor=cmd_builders.SERVER, remove_home=remove_home,
            ),
            sudo=True,
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
        if nopasswd_sudo:
            await self.remove_account_sudoers(login)

    async def remove_management_sudoers(self, management_user: str) -> None:
        """Снести `/etc/sudoers.d/<user>-management` управляющей учётки.

        Парный шаг к `delete_user` на cutover'е: bootstrap кладёт NOPASSWD-правило
        в отдельный sudoers-drop-in, и после удаления самой учётки осиротевший
        файл нужно убрать, чтобы он не висел висячей записью. Idempotent: `rm -f`
        не падает на отсутствующем файле. `management_user` подставляется в путь
        напрямую — безопасно только потому, что прошёл `_validate_login`.
        """
        self._validate_login(management_user)
        sudoers_path = f"/etc/sudoers.d/{management_user}-management"
        rc, _out, stderr = await self.run(f"rm -f {sudoers_path}", sudo=True)
        if rc != 0:
            raise SshError(
                error_code="SSH_USERDEL_FAILED",
                host=self.host,
                cmd_sanitized=f"rm sudoers <{management_user}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=f"sudoers cleanup exit code {rc}",
            )

    async def detect_management_mode(self) -> str:
        """Определить режим создания управляющей учётки по редакции ОС на боксе.

        Одной SSH-командой собираем сигналы и печатаем их помеченными
        строками, разбор — в чистой `parse_management_mode`:

          * Astra-маркеры — наличие `/etc/astra_version`,
            `/etc/astra/build_version`, `/etc/astra-release` либо `ID=astra`
            в `/etc/os-release`;
          * уровень защищённости — `astra-modeswitch get` (числом 0/1/2),
            fallback на `MODE`/`mode` из `/etc/parsec/mswitch.conf`.

        Команда без sudo и без побочных эффектов (только чтение). На non-zero
        или SSH-ошибке (`_capture_text` не raise'ит) считаем ОС не-Астрой и
        отдаём `other_os` — bootstrap тогда возьмёт конфиг общего режима.
        """
        probe = cmd_builders.build_detect_management_mode_probe()
        captured = await self._capture_text(probe)
        if not isinstance(captured, dict) or "error" in captured:
            return MODE_OTHER_OS
        return parse_management_mode(captured.get("stdout", ""))

    async def bootstrap_management_user(
        self, management_user: str, public_key: str,
        *,
        groups: list[str] | None = None,
        extra_create_commands: list[str] | None = None,
        management_private_key=None,
        management_password: str | None = None,
        harden_sshd: bool = False,
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
        1. `useradd -m -s /bin/bash -G <sudo-group>[,<extra>...] <management_user>`
           (idempotent — уже существующий пользователь синхронизируется как
           usermod, пароль не трогаем, ключ ниже всё равно доложим). Sudo-группа
           подбирается по дистрибутиву: `sudo` на Debian/Ubuntu/Astra,
           `wheel` на RHEL/Alpine — пробуем sudo первым, при provision-fail
           откатываемся на wheel. `groups` — доп-группы из пер-режимного
           конфига управляющей учётки (например для конкретной редакции
           Astra), доклеиваются к sudo-группе. Если pre-check показал нужную
           sudo-группу, useradd **skip'ается**, но доп-группы тогда
           досинхронизируются отдельным usermod.
        2. пишем `/etc/sudoers.d/<management_user>-management` с правилом
           `NOPASSWD: ALL` — на управляющей сессии пароля нет (заходим по
           ключу), поэтому sudo обязан работать без него;
        3. создаём `~/.ssh` с правами 700 и `authorized_keys` 600;
        4. дописываем `public_key` в authorized_keys, если его там ещё нет;
        4.5. прогоняем `extra_create_commands` из пер-режимного конфига
           (например выставление уровней целостности для Смоленска) под sudo
           управляющей сессии. Команды задал администратор в конфиге — мы их
           не парсим, выполняем как обычные prepare-шаги. Сами по себе они не
           идемпотентны на уровне worker'а — это ответственность администратора;
           worker лишь гарантирует, что юзер/sudo/ключ уже на месте к моменту
           их запуска.

        Повторный prepare не падает: pre-check короткой дорогой обходит
        шаг 1, иначе useradd на existing → usermod, sudoers-файл
        перезаписывается, а ключ добавляется только при отсутствии
        (grep по точному совпадению строки).

        4.6. Если задан `management_password` — ставим его управляющему
           пользователю через `chpasswd`. Управление дальше идёт по ключу
           (sudo через NOPASSWD), но пароль нужен для console-логина оператором
           и как fallback, если NOPASSWD-drop-in слетит. Пароль свой на каждом
           сервере и приходит из server_service (mgmt_install). Нет пароля —
           шаг пропускается (back-compat). `public_key` — аргумент для
           безопасной записи через here-doc на stdin (не подставляется в
           командную строку, чтобы спецсимволы ключа / комментария не ломали
           shell).

        5. Если задан `management_private_key` — проверяем, что вход под
           управляющим пользователем по этому ключу реально работает (новый
           короткий коннект отдельной сессией). Это анти-локаут: пароль и
           root-login выключаем ТОЛЬКО после подтверждённого входа по ключу.
           Ключ принимаем PEM-строкой (per-server материал) либо путём к файлу.
        6. Если `harden_sshd=True` и проверка ключа прошла — кладём drop-in
           `/etc/ssh/sshd_config.d/<management_user>-dbos.conf`
           (`PubkeyAuthentication yes`, `PasswordAuthentication no`,
           `PermitRootLogin no`) и перезагружаем sshd. Основной sshd_config не
           правим. Если drop-in не подключён в основном конфиге (старый
           `#Include`) — раскомментируем строку Include, иначе snippet
           проигнорируется.
        """
        self._validate_login(management_user)
        # Pre-валидируем ключ ДО useradd/sudoers (битый ключ — фейл setup'а
        # без побочных эффектов на /etc). Канонический валидатор —
        # `_install_authorized_key` (вызывается ниже на шаге 3); зовём его
        # сюда же общей функцией, чтобы пре-чек и итоговая установка
        # держались на одном источнике истины.
        _validate_ssh_public_key(public_key, host=self.host)

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
        # Доп-группы из пер-режимного конфига. Их валидирует `_resolve_groups`
        # внутри create_user/modify_user — здесь только нормализуем None → [].
        extra_groups = list(groups or [])
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
                # useradd пропущен, но доп-группы режима всё равно
                # досинхронизируем — usermod -G доклеит их к уже имеющейся
                # sudo-группе. Без extra_groups usermod — no-op (см. modify_user).
                if extra_groups:
                    await self.modify_user(
                        management_user,
                        groups=sorted(existing_groups | set(extra_groups)),
                        has_sudo=False,
                    )

        if not provisioned:
            for sudo_group in ("sudo", "wheel"):
                try:
                    await self.create_user(
                        management_user,
                        groups=[sudo_group, *extra_groups],
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
        sudoers_line = cmd_builders.build_sudoers_line(management_user)
        rc, _out, stderr = await self.run(
            cmd_builders.build_sudoers_install(sudoers_path),
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

        # 4.5. Пер-режимные команды администратора (например уровни целостности
        # для Смоленска). Выполняются после того, как юзер/sudo/ключ уже на
        # месте — команда вправе опираться на готовую управляющую учётку.
        # sudo=True: команды конфигурят систему (parsec-уровни, pdpl-su и т.п.).
        await self._run_extra_create_commands(
            management_user, extra_create_commands or [],
        )

        # 4.6. Пароль управляющему пользователю (per-server, из mgmt_install).
        # Нужен для console-логина оператором и как fallback к NOPASSWD-sudo.
        # `set_password` идёт через sudo chpasswd; на bootstrap-сессии (пароль
        # есть) sudo читает его со stdin, на ключевой (re-bootstrap) — NOPASSWD.
        if management_password:
            await self.set_password(management_user, management_password)

        # 5. Анти-локаут: до того как трогать парольную аутентификацию, на
        # отдельной сессии убеждаемся, что вход под управляющим пользователем
        # по его ключу реально проходит. Если ключ не пускает (sshd запускается
        # не от root, ключ не совпал, home с неверными правами) — НЕ хардим,
        # бросаем понятную ошибку, оставляя парольный SSH рабочим.
        if management_private_key:
            await self._verify_management_key_login(
                management_user, management_private_key,
            )

        # 6. Хардинг sshd через drop-in. Только после подтверждённого входа по
        # ключу. Без проверки ключа выше (ключ не задан) хардить нельзя — иначе
        # рискуем выключить пароль на сервере, куда ключом не зайти.
        if harden_sshd and management_private_key:
            await self._harden_sshd(management_user)

    async def _run_extra_create_commands(
        self, management_user: str, commands: list[str],
    ) -> None:
        """Прогнать пер-режимные bootstrap-команды администратора под sudo.

        Команды приходят из конфига управляющей учётки (на боксе их не
        формируем). Каждую выполняем как отдельный sudo-шаг; первая упавшая
        (rc != 0) останавливает prepare с `SSH_PREPARE_FAILED` — половинчатый
        bootstrap лучше провалить явно, чем оставить сервер в недонастроенном
        состоянии без сигнала оператору. Команды НЕ парсим и не санитайзим:
        это доверенный ввод администратора (PUT конфига гейтится
        account_admin'ом), а shell-синтаксис на стороне бокса.
        """
        for index, raw in enumerate(commands):
            command = raw.strip()
            if not command:
                continue
            rc, _out, stderr = await self.run(command, sudo=True)
            if rc != 0:
                raise SshError(
                    error_code="SSH_PREPARE_FAILED",
                    host=self.host,
                    cmd_sanitized=f"extra_create_command[{index}] <{management_user}>",
                    returncode=rc,
                    stderr=stderr.strip(),
                    message=f"mode-specific create command exit code {rc}",
                )

    async def _verify_management_key_login(
        self, management_user: str, management_private_key,
    ) -> None:
        """Открыть короткую key-сессию под управляющим пользователем.

        Анти-локаут перед sshd-хардингом: bootstrap-сессия идёт под одноразовым
        паролем, а после хардинга парольный вход выключается — поэтому до
        выключения пароля нужно убедиться, что ключевой вход уже работает.
        Делаем независимый коннект (новый `SshClient`) с тем же host/port, но
        под `management_user` и приватным ключом, выполняем дешёвый `true`.

        `management_private_key` — per-server материал: PEM-строку импортируем в
        key-объект (asyncssh трактует голую строку как путь к файлу), путь к
        файлу оставляем как есть.

        На любой провал коннекта/команды — `SshError(
        SSH_MANAGEMENT_KEY_VERIFY_FAILED)` с actionable-сообщением; sshd при
        этом не тронут, парольный доступ остаётся.
        """
        client_key = _coerce_private_key(management_private_key, self.host)
        try:
            async with SshClient(
                host=self.host,
                username=management_user,
                password=None,
                port=self.port,
                timeout=self.timeout,
                client_keys=[client_key],
            ) as verify_ssh:
                rc, _out, _err = await verify_ssh.run("true")
        except SshError as exc:
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_VERIFY_FAILED",
                host=self.host,
                cmd_sanitized=f"verify key login <{management_user}>",
                message=(
                    "key-based login as the management user did not work after "
                    "bootstrap; refusing to disable password auth (anti-lockout)"
                ),
                details={"underlying_error_code": exc.error_code},
            ) from exc
        if rc != 0:
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_VERIFY_FAILED",
                host=self.host,
                cmd_sanitized=f"verify key login <{management_user}>",
                returncode=rc,
                message=(
                    "management key login session returned non-zero; refusing "
                    "to disable password auth (anti-lockout)"
                ),
            )

    async def _harden_sshd(self, management_user: str) -> None:
        """Положить hardening drop-in в `/etc/ssh/sshd_config.d/` и reload sshd.

        Снаружи основной `sshd_config` не правим — кладём отдельный snippet
        `<management_user>-dbos.conf` с `PubkeyAuthentication yes`,
        `PasswordAuthentication no`, `PermitRootLogin no`. Имя файла берёт
        управляющего пользователя как уникальный суффикс (он уже прошёл
        `_validate_login`, поэтому безопасен для подстановки в путь).

        Перед записью убеждаемся, что drop-in вообще подключён: на части
        дистрибутивов строка `Include /etc/ssh/sshd_config.d/*.conf` в основном
        конфиге закомментирована — без неё snippet прочитан не будет.
        Раскомментируем её (idempotent: sed правит только закомментированный
        вариант).

        Snippet проверяется `sshd -t` ДО reload'а — битый конфиг не катим
        (иначе sshd не перезапустится и сервер останется без SSH). Reload
        подбираем по доступному инструменту: `systemctl reload sshd` →
        `service ssh reload` → `rc-service sshd reload` → `kill -HUP` демона.
        """
        # Snippet идёт на stdin `tee`, не в командную строку. Сначала пишем во
        # временный файл, валидируем `sshd -t` против собранного конфига и
        # только при успехе перемещаем на место. `management_user` уже прошёл
        # `_validate_login`, путь безопасен.
        dropin_path = f"/etc/ssh/sshd_config.d/{management_user}-dbos.conf"
        snippet = cmd_builders.SSHD_HARDEN_SNIPPET
        # Билдер собирает bash: раскомментирует `Include` при необходимости,
        # кладёт snippet во временный путь ВНУТРИ sshd_config.d, атомарно
        # переносит, валидирует собранный конфиг доступным `sshd -t` и
        # откатывает drop-in при провале. Snippet уходит на stdin `tee`.
        rc, _out, stderr = await self.run(
            cmd_builders.build_sshd_harden(dropin_path),
            sudo=True,
            stdin_payload=snippet,
        )
        if rc != 0:
            raise SshError(
                error_code="SSH_HARDEN_FAILED",
                host=self.host,
                cmd_sanitized=f"harden sshd <{management_user}>",
                returncode=rc,
                stderr=stderr.strip(),
                message=(
                    "sshd hardening snippet failed validation (sshd -t); "
                    "password auth left enabled"
                ),
            )
        await self._reload_sshd(management_user)

    async def _reload_sshd(self, management_user: str) -> None:
        """Перечитать конфиг sshd, перебрав доступные механизмы reload'а.

        Reload (а не restart) не рвёт активные сессии. Пробуем по порядку
        `systemctl` (systemd), `service` (SysV/Debian), `rc-service`
        (OpenRC/Alpine), затем fallback на `kill -HUP` мастер-процесса sshd.
        Первый, вернувший rc=0, выигрывает. Если ни один не сработал — конфиг
        уже валиден (прошёл `sshd -t`) и применится при следующем старте sshd,
        поэтому не валим prepare, а пишем warning.
        """
        reload_cmds = cmd_builders.build_sshd_reload_commands()
        for cmd in reload_cmds:
            rc, _out, _err = await self.run(cmd, sudo=True)
            if rc == 0:
                return
        logger.warning(
            "sshd reload did not succeed on %s after hardening; config is "
            "valid and will apply on next sshd start", self.host,
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
        if not _LOGIN_RE.fullmatch(login):
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
        if not _PATH_RE.fullmatch(value):
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
            if not _GROUP_RE.fullmatch(g):
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

        Набор команд (hostname/uname/lscpu/lsblk/df/meminfo/ip/virt-проба/
        os-release/lspci/astra build+license/apt sources) и сборка блоков живут
        в `src.tasks._inventory_common.collect_inventory` — один код на прямой
        сервер (этот метод, через `DirectRunner`) и на гостя ВМ
        (`tasks.vms_inventory`, через `GuestHopRunner`).

        Возврат — dict с ключами `hostname`, `kernel`, `cpu`, `disks`, `df`,
        `meminfo`, `net_interfaces`, `virtualization`, `os`, `pci`,
        `astra_build`, `astra_license`, `apt_sources`. Каждый блок best-effort:
        упавшая команда кладёт `error`, остальные целы. Astra-блоки на не-Астре
        ожидаемо приходят с `error` (файлов нет). Разбор в flat-payload —
        `services.ssh_client.inventory_facts_to_payload`.
        """
        # Ленивый импорт: `_inventory_common` импортирует из этого модуля
        # (`SshError`/`_parse_os_release`), поэтому на module-top импорт
        # обратно замкнул бы цикл. К моменту вызова оба модуля загружены.
        from src.tasks._inventory_common import collect_inventory
        from src.tasks._target_runner import DirectRunner

        return await collect_inventory(DirectRunner(self))

    # ── User inventory: getent passwd / group / sudoers ─────────────────

    async def get_os_users(self) -> dict:
        """Собрать список реальных OS-пользователей сервера.

        Команды (`getent passwd` / `getent group` / `cat /etc/login.defs`) и
        сборка блоков — в `src.tasks._inventory_common.collect_os_users`, общем
        для прямого сервера (этот метод, `DirectRunner`) и гостя ВМ
        (`GuestHopRunner`). sudo-членство определяется по `getent group`,
        отдельного `sudo -l -U <login>` нет (дорого и требует root).

        Возврат — dict с ключами `passwd`, `group`, `login_defs` (каждый —
        `_capture_text`-результат). Парсинг и UID-фильтр — на стороне
        `services.ssh_client.os_users_facts_to_payload`.
        """
        from src.tasks._inventory_common import collect_os_users
        from src.tasks._target_runner import DirectRunner

        return await collect_os_users(DirectRunner(self))

    async def _capture_text(self, command: str) -> dict:
        """Запустить команду, вернуть `{stdout, stderr, returncode}` либо
        `{error: ..., returncode}` при non-zero. Не raise'ит — caller
        видит частичный результат.

        Используется Astra-mode-пробой (`_check_astra_mode`); тот же по логике
        сборщик поверх `runner.run` живёт в `tasks._inventory_common`.
        """
        try:
            rc, out, err = await self.run(command)
        except SshError as exc:
            return {"error": f"{exc.error_code}: {exc.message}", "returncode": None}
        if rc != 0:
            return {"error": err.strip() or f"exit code {rc}", "returncode": rc, "stdout": out}
        return {"stdout": out.strip(), "stderr": err.strip(), "returncode": rc}


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
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out
