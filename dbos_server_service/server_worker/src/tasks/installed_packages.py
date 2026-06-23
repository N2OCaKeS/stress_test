"""Live-список установленных пакетов сервера через SSH.

В отличие от `inventory.sync` (full snapshot — kernel/cpu/disks/os/packages),
эта таска точечная: получаем только пакеты по glob-pattern и возвращаем
плоский список `{packages: [{name, version}, ...]}`. server_service её
вызывает из `GET /servers/{id}/installed-packages?pattern=...` и НЕ
сохраняет результат в БД — каждый запрос идёт live.

Поддержаны разные пакетные бэкенды (фронтенды apt/dnf/yum/zypper работают
поверх dpkg/rpm, их отдельно не кодим):

* `dpkg` — Debian/Ubuntu/Astra Linux → `dpkg-query -W -f='${Package}
  ${Version}\\n' '<pattern>'`. Пустой output = ничего не подходит под
  pattern (rc=0 в dpkg-query).
* `rpm` — RHEL/CentOS/Fedora → `rpm -qa --queryformat '%{NAME}
  %{VERSION}\\n' '<pattern>'`.
* `apk` — Alpine Linux → `apk info -v` (выдаёт по строке на пакет в
  формате `<name>-<version>`).
* `pacman` — Arch/Manjaro → `pacman -Q` (строки `<name> <version>`, как
  dpkg).
* `portage` — Gentoo → `qlist -Iv` (строки `<category>/<name>-<version>`).
* `xbps` — Void Linux → `xbps-query -l` (строки `ii <name>-<version>
  <description>`).

Инструменты, не умеющие glob по pattern (apk/pacman/portage/xbps), листят
все пакеты, а фильтрация по pattern делается в Python (`_filter_by_pattern`,
`fnmatch`). Если ни один менеджер не найден —
`SshError(error_code="NO_PACKAGE_MANAGER")`.

Pattern — shell glob (`htop`, `linux-image*`). asyncssh.run запускает
команду через `/bin/sh -c`, поэтому pattern оборачивается в одиночные
кавычки. Перед подстановкой worker сам валидирует его по allow-list'у
`[A-Za-z0-9._\\-+*?\\[\\]]+` (`_PATTERN_RE`) — это defence-in-depth, не
полагаемся на то, что server_service отсёк `'`/`$`/`;` и прочие
метасимволы, через которые можно вырваться из кавычек. Для apk pattern в
shell вообще не уходит — фильтрация чисто Python-side.
"""

from __future__ import annotations

import fnmatch
import logging
import re

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.main import broker
from src.services import ssh_client
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# server_id/count/package_manager — операционные счётчики без секретов и без
# намёка на интент оператора. `pattern` живёт в отдельном whitelist'е и
# уходит в audit-details только при включённом
# `AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG` — иначе оператор, запросивший
# `linux-image*` или `openssl*`, оставлял бы CVE-релевантный фокус в audit
# (мягкая разведка для атакующего с доступом к loging). Полный результат
# (`packages`) наружу всё равно не уходит — лежит в task.result, виден
# через API. То же решение, что в `tasks/inventory.py::AUDIT_SAFE_FIELDS`.
_BASE_AUDIT_SAFE_FIELDS: frozenset[str] = frozenset(
    {"server_id", "count", "package_manager"},
)


def _audit_safe_fields() -> set[str]:
    """Собрать whitelist под текущую конфигурацию.

    Default — без `pattern`. Если оператор явно включил debug-флаг через
    env, `pattern` добавляется в whitelist и попадает в audit-details.
    Settings read через `get_settings()` (lru_cache) — дёшево. Кэш сюда
    добавлять опасно: тесты гоняют `get_settings.cache_clear()` между
    кейсами, а отдельный module-level cache их перетёр бы.
    """
    fields = set(_BASE_AUDIT_SAFE_FIELDS)
    if get_settings().audit_installed_packages_pattern_debug:
        fields.add("pattern")
    return fields


# Backward-compat alias для тестов / внешних читателей, ожидавших
# module-level set. Содержит дефолтный (masked) набор полей.
AUDIT_SAFE_FIELDS: set[str] = set(_BASE_AUDIT_SAFE_FIELDS)

# Локальный allow-list символов для glob-pattern перед подстановкой в
# shell-команду. Принимаем только то, что нужно dpkg-query/rpm glob'у:
# буквы, цифры, точка, подчёркивание, дефис, плюс и сами glob-метасимволы
# `* ? [ ]`. Кавычка `'`, `$`, `;`, `\`, backtick, пробелы, перевод строки
# не проходят — это закрывает break-out из одиночных кавычек и shell-
# инъекцию. defence-in-depth: не полагаемся на валидацию server_service.
# Тот же класс символов заявлен в docstring модуля и в server_service.
_PATTERN_RE = re.compile(r"^[A-Za-z0-9._\-+*?\[\]]+$")

# Connect-time коды SshClient.connect(), означающие «до сервера дошли, но
# управляющая SSH-сессия не поднялась»: auth не прошёл, коннект сорвался,
# handshake завис. На ПОДГОТОВЛЕННОМ (managed) сервере это почти всегда значит,
# что бокс переустановили (reimage) — управляющий пользователь/ключ на нём
# пропали, хотя в БД сервер ещё `is_managed=true`. Сырой SSH_AUTH_FAILED с
# пустыми cmd/stderr оператору ничего не объясняет, поэтому ремапим в один
# actionable код с понятным текстом.
_CONNECT_FAILURE_CODES = frozenset(
    {"SSH_AUTH_FAILED", "SSH_CONNECT_FAILED", "SSH_TIMEOUT"},
)


def _remap_managed_connect_error(exc: SshError) -> SshError:
    """Превратить connect-фейл управляющей сессии в actionable ошибку.

    Зовётся, когда сервер помечен managed, но `SshClient.connect()` не смог
    приконнектиться/авторизоваться. Возвращает новый `SshError` со стабильным
    `error_code="SERVER_MANAGEMENT_AUTH_FAILED"` и человекочитаемым сообщением,
    подсказывающим оператору, что ОС, скорее всего, переустановлена и нужен
    повторный prepare. Исходный код и host сохраняем в `details` для
    диагностики; креды не светим — `SshError.connect()` их в message не кладёт.
    """
    return SshError(
        error_code="SERVER_MANAGEMENT_AUTH_FAILED",
        host=exc.host,
        message=(
            "управляющая SSH-сессия к подготовленному серверу не поднялась "
            "(возможно, ОС на сервере переустановлена и управляющий "
            "пользователь/ключ утрачены) — требуется повторный prepare"
        ),
        details={"underlying_error_code": exc.error_code},
    )


def _build_command(package_manager: str, pattern: str) -> str:
    """Собрать shell-команду под выбранный package manager.

    Pattern оборачивается в одинарные кавычки. Перед подстановкой он
    обязан пройти `_PATTERN_RE` (caller валидирует) — внутри не может быть
    `'`/`$`/`;` и прочих метасимволов, поэтому break-out из кавычек
    невозможен.
    """
    if package_manager == "dpkg":
        return (
            "dpkg-query -W -f='${Package} ${Version}\\n' '"
            + pattern
            + "' 2>/dev/null"
        )
    if package_manager == "rpm":
        return (
            "rpm -qa --queryformat '%{NAME} %{VERSION}\\n' '"
            + pattern
            + "' 2>/dev/null"
        )
    if package_manager == "apk":
        # apk не глоббит pattern на стороне инструмента — листим всё, а
        # фильтрацию по pattern делаем уже в Python (`_filter_by_pattern`).
        # Поэтому pattern в shell не подставляем.
        return "apk info -v 2>/dev/null"
    if package_manager == "pacman":
        # pacman -Q выдаёт `name version` по строке, как dpkg, и сам не
        # глоббит — листим всё, фильтр по pattern в Python.
        return "pacman -Q 2>/dev/null"
    if package_manager == "portage":
        # qlist -Iv (portage-utils) — строки `category/name-version`.
        # Фильтр по pattern в Python.
        return "qlist -Iv 2>/dev/null"
    if package_manager == "xbps":
        # xbps-query -l — строки `ii name-version_rev  description`.
        # Фильтр по pattern в Python.
        return "xbps-query -l 2>/dev/null"
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host="",
        message=f"unsupported package manager: {package_manager}",
    )


def _parse_packages(
    stdout: str, package_manager: str = "dpkg"
) -> list[dict[str, str]]:
    """Разобрать вывод package manager'а в список `[{name, version}, ...]`.

    Для dpkg-query / rpm -qa / pacman формат строки — `<name> <version>`
    (две колонки, разделитель пробел). Для apk (`apk info -v`) формат
    другой — одна слитная строка `<name>-<version>-r<rel>`, разбирается
    отдельно (`_parse_apk_line`). portage (`qlist -Iv`) и xbps
    (`xbps-query -l`) тоже со своими парсерами.

    Один пакет может встретиться несколько раз (например, разные версии
    `linux-image-*`) — отдаём как есть, без дедупликации: server_service /
    UI решает, как показывать (свернуть в array или поднять conflict).

    Пустая строка / нераспознанная строка — пропускаем (защита от мусора).
    """
    packages: list[dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if package_manager == "apk":
            packages.append(_parse_apk_line(line))
            continue
        if package_manager == "portage":
            packages.append(_parse_portage_line(line))
            continue
        if package_manager == "xbps":
            parsed = _parse_xbps_line(line)
            if parsed is not None:
                packages.append(parsed)
            continue
        # `split(None, 1)` — разделяем по первому пробелу/табу, остальное
        # остаётся в version. dpkg-query format формально с одним пробелом,
        # но defensive split на whitespace надёжнее.
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name, version = parts
        packages.append({"name": name, "version": version})
    return packages


def _parse_apk_line(line: str) -> dict[str, str]:
    """Разобрать одну строку `apk info -v` в `{name, version}`.

    Формат — `<name>-<version>-r<rel>`, где имя пакета само может содержать
    дефисы (`py3-pip-23.1-r0`). Версия всегда занимает два последних
    дефис-сегмента (`<version>-r<rel>`), поэтому режем справа на 3 части:
    `py3-pip-23.1-r0` → `["py3-pip", "23.1", "r0"]` → name=`py3-pip`,
    version=`23.1-r0`. Если revision-сегмента нет (нестандартная строка),
    fallback: режем справа один раз; если и это не делится — вся строка
    уходит в name с пустой version.
    """
    parts = line.rsplit("-", 2)
    if len(parts) == 3:
        name, ver, rel = parts
        return {"name": name, "version": f"{ver}-{rel}"}
    parts = line.rsplit("-", 1)
    if len(parts) == 2:
        return {"name": parts[0], "version": parts[1]}
    return {"name": line, "version": ""}


# Версия в portage-атоме начинается с цифры сразу после дефиса. Имя берём
# non-greedy, чтобы дефисы внутри имени (`libfoo-bar`) не съелись в версию.
_PORTAGE_RE = re.compile(r"^(.*?)-(\d.*)$")


def _parse_portage_line(line: str) -> dict[str, str]:
    """Разобрать одну строку `qlist -Iv` в `{name, version}`.

    Формат — `<category>/<name>-<version>`, напр. `app-shells/bash-5.2_p15`
    или `dev-python/pip-23.1-r1`. Категорию отбрасываем (basename после
    последнего `/`), затем делим имя и версию по первому дефису, за которым
    идёт цифра: `bash-5.2_p15` → name=`bash`, version=`5.2_p15`;
    `pip-23.1-r1` → name=`pip`, version=`23.1-r1`; `libfoo-bar-1.2` →
    name=`libfoo-bar`, version=`1.2`. Если версии нет — вся строка в name.
    """
    basename = line.rsplit("/", 1)[-1]
    m = _PORTAGE_RE.match(basename)
    if m:
        return {"name": m.group(1), "version": m.group(2)}
    return {"name": basename, "version": ""}


def _parse_xbps_line(line: str) -> dict[str, str] | None:
    """Разобрать одну строку `xbps-query -l` в `{name, version}`.

    Формат — `ii <name>-<version>_<rev>   <description>`: первый токен —
    состояние (`ii`), второй — `name-version_rev`, дальше описание. Берём
    токен с индексом 1 и режем по последнему дефису: `bash-5.2.015_1` →
    name=`bash`, version=`5.2.015_1`; `python3-pip-23.1_1` →
    name=`python3-pip`, version=`23.1_1`. Строки с менее чем двумя
    токенами пропускаем (возвращаем `None`).
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None
    name, _, version = tokens[1].rpartition("-")
    if not name:
        return {"name": tokens[1], "version": ""}
    return {"name": name, "version": version}


def _filter_by_pattern(
    packages: list[dict[str, str]], pattern: str
) -> list[dict[str, str]]:
    """Отфильтровать пакеты по glob-pattern на стороне Python.

    Нужно для apk, который сам не глоббит — листит все пакеты, фильтруем
    здесь. Пустой pattern или `*` — без фильтра (вернуть всё). Сравнение
    через `fnmatch.fnmatchcase` — case-sensitive и не зависит от ОС
    воркера, в отличие от `fnmatch.fnmatch`. Это согласуется с tool-side
    глоббингом dpkg-query/rpm, который тоже регистрозависим.
    """
    if not pattern or pattern == "*":
        return packages
    return [p for p in packages if fnmatch.fnmatchcase(p["name"], pattern)]


async def _detect_package_manager(ssh: SshClient) -> str:
    """Определить package manager на удалённом хосте.

    `command -v` возвращает rc=0 и путь если бинарь есть в PATH, rc!=0
    если нет. Используем именно `command -v`, а не `which`: POSIX-стандарт,
    есть и в Debian, и в RHEL, и в Alpine без отдельной установки.

    Порядок проверки: dpkg → rpm → apk → pacman → portage → xbps. Если
    есть несколько (теоретически возможно на гибридных хостах с alien) —
    берём первый по приоритету; dpkg первый для Astra Linux target'а.
    portage детектим по `qlist` (portage-utils — стандарт для скриптинга
    на Gentoo); если qlist нет, portage не выбираем, даже если есть emerge.
    """
    rc, _, _ = await ssh.run("command -v dpkg-query")
    if rc == 0:
        return "dpkg"
    rc, _, _ = await ssh.run("command -v rpm")
    if rc == 0:
        return "rpm"
    rc, _, _ = await ssh.run("command -v apk")
    if rc == 0:
        return "apk"
    rc, _, _ = await ssh.run("command -v pacman")
    if rc == 0:
        return "pacman"
    rc, _, _ = await ssh.run("command -v qlist")
    if rc == 0:
        return "portage"
    rc, _, _ = await ssh.run("command -v xbps-query")
    if rc == 0:
        return "xbps"
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host=ssh.host,
        message=(
            "no supported package manager "
            "(dpkg-query/rpm/apk/pacman/qlist/xbps-query) on remote host"
        ),
    )


@broker.task("installed_packages.list")
async def installed_packages_list(task_id: str) -> None:
    """Получить список установленных пакетов по glob-pattern.

    Что делает: подключается по SSH (через дефолтный root либо account_id
    из payload, если задан), определяет package manager
    (`dpkg`/`rpm`/`apk`/`pacman`/`portage`/`xbps`), выполняет
    соответствующую команду листинга, возвращает плоский список
    `{name, version}`-словарей. Для менеджеров без tool-side glob
    (apk/pacman/portage/xbps) pattern фильтруется в Python.

    Параметры: `task_id`. Payload — `server_id` (обязательно), `pattern`
    (default `*`), опционально `account_id`, `ssh_login`, `ssh_host`,
    `target_department_id`.

    Возвращает: `{server_id, pattern, package_manager, count, packages: [...]}`.
    audit-detail'и режутся whitelist'ом `_audit_safe_fields()` — список
    пакетов наружу в loging_service не уходит. `pattern` по умолчанию
    тоже не уходит (CVE-recon hint), показывается только если включён
    `AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG`.

    Возможные ошибки: `SshError(SSH_AUTH_FAILED/SSH_CONNECT_FAILED/...)`,
    `SshError(NO_PACKAGE_MANAGER)`, `CredentialFetchError`.

    Связано с: server_service `POST /servers/{id}/installed-packages`,
    audit action `installed_packages.list`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        pattern = payload.get("pattern", "*")
        account_id = payload.get("account_id")
        target_dept = payload.get("target_department_id")
        is_managed = bool(payload.get("is_managed"))

        # Defence-in-depth: pattern уходит в shell-команду (asyncssh.run
        # через /bin/sh -c). Проверяем локально, не доверяя валидации
        # server_service — отклоняем кавычки/$/;/метасимволы до подстановки.
        if not isinstance(pattern, str) or not _PATTERN_RE.match(pattern):
            raise SshError(
                error_code="INVALID_PATTERN",
                host=str(payload.get("ssh_host") or server_id),
                message=(
                    f"pattern {pattern!r} contains characters disallowed "
                    "for a package glob (only [A-Za-z0-9._-+*?[]] allowed)"
                ),
            )

        # На управляемом сервере вход по ключу под management_user — пароль
        # аккаунта не нужен; self-сценарий — пароль из server_service.
        # Общая логика в `_account_helpers.resolve_ssh_creds` (тот же паттерн
        # в inventory.sync / users.inventory).
        creds = await resolve_ssh_creds(
            payload,
            server_id,
            account_id=account_id,
            target_dept=target_dept,
            is_managed=is_managed,
        )

        # is_managed/management_user/host/ssh_port из payload пропускаются
        # через единый apply_session_hints — server_service кладёт туда
        # server.ip_address как `host` и `ssh_port` для нестандартного порта.
        ssh_client.apply_session_hints(creds, payload)

        host = creds.get("host") or creds.get("ssh_host") or server_id
        # `%r` для host — defence-in-depth от log-injection: host приходит из
        # creds (server_service/payload-hints), теоретически может содержать
        # `\n`. `pattern` уже прошёл `_PATTERN_RE`, безопасен.
        logger.info("installed_packages.list on %r pattern=%s", host, pattern)
        session = ssh_client.build_session(creds, server_id)
        try:
            await session.connect()
        except SshError as exc:
            # На managed-сервере connect-фейл (auth/connect/timeout) почти
            # всегда означает reimage бокса — даём оператору actionable
            # сообщение вместо сырого SSH_AUTH_FAILED. На неуправляемом
            # (self-сессия по паролю) оставляем исходную ошибку — там это
            # просто неверный пароль аккаунта, не повод звать prepare.
            if is_managed and exc.error_code in _CONNECT_FAILURE_CODES:
                await session.close()
                raise _remap_managed_connect_error(exc) from exc
            await session.close()
            raise
        async with session as ssh:
            package_manager = await _detect_package_manager(ssh)
            cmd = _build_command(package_manager, pattern)
            rc, stdout, stderr = await ssh.run(cmd)
            if rc != 0:
                # dpkg-query возвращает rc=1 если ничего не нашлось —
                # это валидный «empty result», stderr пустой. rc!=0 со
                # stderr — реальная ошибка (broken DB, нет binary'я).
                if stderr.strip():
                    raise SshError(
                        error_code="PACKAGE_QUERY_FAILED",
                        host=host,
                        cmd_sanitized=cmd,
                        returncode=rc,
                        stderr=stderr.strip(),
                        message=f"{package_manager} query failed",
                    )
                # rc!=0 без stderr — трактуем как «ничего не подошло».
                stdout = ""

        packages = _parse_packages(stdout, package_manager)
        # apk/pacman/portage/xbps листят все пакеты — фильтр по pattern
        # делаем здесь. Для dpkg/rpm pattern уже отработал на стороне
        # инструмента, повторно не фильтруем.
        if package_manager in ("apk", "pacman", "portage", "xbps"):
            packages = _filter_by_pattern(packages, pattern)
        return {
            "server_id": server_id,
            "pattern": pattern,
            "package_manager": package_manager,
            "count": len(packages),
            "packages": packages,
        }

    await run_task(
        task_id,
        audit_action="installed_packages.list",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=_audit_safe_fields(),
    )
