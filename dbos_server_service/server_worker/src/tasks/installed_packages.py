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

Можно запросить несколько паттернов сразу — пакет попадает в результат,
если матчит ЛЮБОЙ из них (OR-матч). dpkg/rpm принимают несколько glob'ов
позиционными аргументами; инструменты, не умеющие glob по pattern
(apk/pacman/portage/xbps), листят все пакеты, а OR-фильтрация делается в
Python (`_filter_by_patterns`, `fnmatch`). Дубли по имени (один пакет под
несколько паттернов) схлопываются (`_dedup_by_name`). Если ни один
менеджер не найден — `SshError(error_code="NO_PACKAGE_MANAGER")`.

Pattern — shell glob (`htop`, `linux-image*`). asyncssh.run запускает
команду через `/bin/sh -c`, поэтому каждый pattern оборачивается в одиночные
кавычки. Перед подстановкой worker сам валидирует его по allow-list'у
`[A-Za-z0-9._\\-+*?\\[\\]]+` (`_PATTERN_RE`) — это defence-in-depth, не
полагаемся на то, что server_service отсёк `'`/`$`/`;` и прочие
метасимволы, через которые можно вырваться из кавычек. Для apk pattern в
shell вообще не уходит — фильтрация чисто Python-side.
"""

from __future__ import annotations

import logging
import re

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.main import broker
from src.services import ssh_client
from src.tasks import _packages_common as pkg
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._runner import run_task
from src.tasks._target_runner import DirectRunner

logger = logging.getLogger(__name__)

# Общая логика листинга пакетов (детект/сборка/парсинг/фильтр) вынесена в
# `_packages_common` — она одна на сервер и на гостя ВМ. Здесь держим алиасы под
# историческими именами: на них ссылаются тесты и `vms.list_packages` (через
# импорт этого модуля).
_PATTERN_RE = pkg.PATTERN_RE
_resolve_patterns = pkg.resolve_patterns
_build_command = pkg.build_command
_parse_packages = pkg.parse_packages
_filter_by_patterns = pkg.filter_by_patterns
_dedup_by_name = pkg.dedup_by_name

# server_id/count/package_manager — операционные счётчики без секретов и без
# намёка на интент оператора. `patterns` живут в отдельном whitelist'е и
# уходят в audit-details только при включённом
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

    Default — без `patterns`. Если оператор явно включил debug-флаг через
    env, `patterns` добавляются в whitelist и попадают в audit-details.
    Settings read через `get_settings()` (lru_cache) — дёшево. Кэш сюда
    добавлять опасно: тесты гоняют `get_settings.cache_clear()` между
    кейсами, а отдельный module-level cache их перетёр бы.
    """
    fields = set(_BASE_AUDIT_SAFE_FIELDS)
    if get_settings().audit_installed_packages_pattern_debug:
        fields.add("patterns")
    return fields


# Backward-compat alias для тестов / внешних читателей, ожидавших
# module-level set. Содержит дефолтный (masked) набор полей.
AUDIT_SAFE_FIELDS: set[str] = set(_BASE_AUDIT_SAFE_FIELDS)

# Allow-list имён пакетов для мутаций (install/remove/upgrade). В отличие от
# glob-pattern'а здесь НЕ допускаем `* ? [ ]` — устанавливать/сносить пакеты
# по маске опасно (один `*` снёс бы пол-системы) и сам apt/dpkg при install
# трактует имя буквально. Разрешённый класс — `[A-Za-z0-9._+-]`: легальные
# имена пакетов Debian/RPM (`linux-image-amd64`, `g++`, `lib32z1`,
# `python3.11`). Метасимволы shell (`'`, `$`, `;`, пробел, backtick) тем
# самым тоже отсечены — break-out из одиночных кавычек невозможен.
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")

# Package manager'ы, для которых поддержана мутация. list работает на всех
# шести бэкендах, но менять состав мы умеем только там, где знаем безопасный
# не-интерактивный синтаксис: dpkg→apt-get, rpm→dnf/yum, apk→apk.
_MUTABLE_PACKAGE_MANAGERS = frozenset({"dpkg", "rpm", "apk"})

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


async def _detect_package_manager(ssh: SshClient) -> str:
    """Определить package manager на прямом сервере (обёртка над общим детектом).

    Оставлена как тонкий адаптер: мутации (install/remove/update) детектят
    менеджер на управляющей SSH-сессии напрямую. Гоняет общий
    `_packages_common.detect_package_manager` через `DirectRunner`.
    """
    return await pkg.detect_package_manager(DirectRunner(ssh))


@broker.task("installed_packages.list")
async def installed_packages_list(task_id: str) -> None:
    """Получить список установленных пакетов по glob-паттернам.

    Что делает: подключается по SSH (через дефолтный root либо account_id
    из payload, если задан), определяет package manager
    (`dpkg`/`rpm`/`apk`/`pacman`/`portage`/`xbps`), выполняет
    соответствующую команду листинга, возвращает плоский список
    `{name, version}`-словарей. Можно передать несколько паттернов —
    пакет попадает в результат, если матчит ЛЮБОЙ (OR). dpkg/rpm глоббят
    несколькими позиционными аргументами; для менеджеров без tool-side
    glob (apk/pacman/portage/xbps) паттерны фильтруются в Python. Дубли по
    имени схлопываются, итог режется по `max_rows`.

    Параметры: `task_id`. Payload — `server_id` (обязательно), `patterns`
    (список glob'ов) либо одиночный `pattern` (back-compat, default `*`),
    `max_rows` (опц. cap на число строк), опционально `account_id`,
    `ssh_login`, `ssh_host`, `target_department_id`.

    Возвращает: `{server_id, patterns, package_manager, count, packages: [...]}`.
    audit-detail'и режутся whitelist'ом `_audit_safe_fields()` — список
    пакетов наружу в loging_service не уходит. `patterns` по умолчанию
    тоже не уходят (CVE-recon hint), показываются только если включён
    `AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG`.

    Возможные ошибки: `SshError(SSH_AUTH_FAILED/SSH_CONNECT_FAILED/...)`,
    `SshError(NO_PACKAGE_MANAGER)`, `CredentialFetchError`.

    Связано с: server_service `POST /servers/{id}/installed-packages`,
    audit action `installed_packages.list`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        patterns = _resolve_patterns(payload)
        max_rows = payload.get("max_rows")
        account_id = payload.get("account_id")
        target_dept = payload.get("target_department_id")
        is_managed = bool(payload.get("is_managed"))

        # Defence-in-depth: паттерны уходят в shell-команду (asyncssh.run
        # через /bin/sh -c). Проверяем каждый локально, не доверяя валидации
        # server_service — отклоняем кавычки/$/;/метасимволы до подстановки.
        for pattern in patterns:
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
        # На управляемом сервере подтянуть per-server управляющий ключ из
        # server_service до сборки сессии (build_session читает его из creds).
        await ssh_client.attach_management_creds(creds, server_id)

        host = creds.get("host") or creds.get("ssh_host") or server_id
        # `%r` для host — defence-in-depth от log-injection: host приходит из
        # creds (server_service/payload-hints), теоретически может содержать
        # `\n`. Паттерны уже прошли `_PATTERN_RE`, безопасны.
        logger.info(
            "installed_packages.list on %r patterns=%s", host, patterns,
        )
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
        # Цель — прямой сервер: общий листинг гоняет команды по этой же
        # управляющей сессии (детект, dpkg-query/rpm/..., парсинг, дедуп, cap).
        async with session as ssh:
            package_manager, packages = await pkg.collect_packages(
                DirectRunner(ssh), patterns, max_rows=max_rows,
            )
        return {
            "server_id": server_id,
            "patterns": patterns,
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


# ── Изменяющие операции: install / remove / update ──────────────────────────

# Whitelist полей для audit-details мутаций. Имена пакетов сами по себе не
# секрет, но это интент оператора (что ставит/сносит) — отдаём в audit явно,
# тут нет CVE-recon'а уровня glob-pattern'а (пакет именован буквально). Полный
# stdout менеджера наружу не уходит — лежит в task.result.
_MUTATION_AUDIT_SAFE_FIELDS: set[str] = {
    "server_id",
    "operation",
    "package_manager",
    "packages",
    "count",
    "returncode",
}


def _validate_package_names(packages: list, host: str) -> list[str]:
    """Проверить список имён пакетов по строгому allow-list'у.

    Возвращает нормализованный список строк. Любой элемент не-str, пустой
    либо с символом вне `_PKG_NAME_RE` валит таску `INVALID_PACKAGE_NAME` —
    раньше, чем имя попадёт в shell-команду. Это defence-in-depth поверх
    валидации server_service: на shell-уровень уходит только то, что прошло
    обе проверки.
    """
    if not isinstance(packages, list) or not packages:
        raise SshError(
            error_code="INVALID_PACKAGE_NAME",
            host=host,
            message="packages must be a non-empty list of names",
        )
    out: list[str] = []
    for name in packages:
        if not isinstance(name, str) or not _PKG_NAME_RE.match(name):
            raise SshError(
                error_code="INVALID_PACKAGE_NAME",
                host=host,
                message=(
                    f"package name {name!r} contains characters disallowed for "
                    "a package name (only [A-Za-z0-9._+-], must start alnum)"
                ),
            )
        out.append(name)
    return out


def _build_mutation_command(
    package_manager: str, operation: str, packages: list[str]
) -> str:
    """Собрать не-интерактивную команду мутации под выбранный package manager.

    `packages` обязаны пройти `_validate_package_names` — внутри только
    `[A-Za-z0-9._+-]`, поэтому подстановка в shell без кавычек безопасна
    (метасимволов нет). update без явного списка пакетов = обновить всё.

    Команды с `&&` (apt-get/apk update перед install) оборачиваются в
    `sh -c '...'`. SSH-слой исполняет шаг под sudo как `sudo -S -p '' <cmd>` —
    без обёртки sudo накрыл бы только первый сегмент до `&&`, а второй
    (install) пошёл бы без прав и упал на dpkg-lock. Внутри одинарных кавычек
    `sh -c` безопасно, потому что имена пакетов прошли allow-list (нет `'`).

    Поддержаны dpkg/rpm/apk; для остального — `SshError`
    (`UNSUPPORTED_PACKAGE_MANAGER`). Все команды не задают вопросов
    (`-y` / `--noninteractive`).
    """
    joined = " ".join(packages)
    if package_manager == "dpkg":
        # DEBIAN_FRONTEND=noninteractive глушит debconf-промпты (postinst).
        env = "DEBIAN_FRONTEND=noninteractive"
        if operation == "install":
            return f"sh -c '{env} apt-get update && {env} apt-get install -y {joined}'"
        if operation == "remove":
            return f"{env} apt-get remove -y {joined}"
        if operation == "update":
            # Без списка пакетов — dist-safe upgrade всего; со списком —
            # apt-get install переустанавливает именно их на свежие версии.
            if packages:
                return f"sh -c '{env} apt-get update && {env} apt-get install -y --only-upgrade {joined}'"
            return f"sh -c '{env} apt-get update && {env} apt-get upgrade -y'"
    elif package_manager == "rpm":
        # dnf на современных RHEL/Fedora; на старых это symlink на yum, синтаксис
        # совпадает. -y подавляет промпты.
        if operation == "install":
            return f"dnf install -y {joined}"
        if operation == "remove":
            return f"dnf remove -y {joined}"
        if operation == "update":
            if packages:
                return f"dnf upgrade -y {joined}"
            return "dnf upgrade -y"
    elif package_manager == "apk":
        if operation == "install":
            return f"sh -c 'apk update && apk add {joined}'"
        if operation == "remove":
            return f"apk del {joined}"
        if operation == "update":
            if packages:
                return f"sh -c 'apk update && apk add --upgrade {joined}'"
            return "sh -c 'apk update && apk upgrade'"
    else:
        raise SshError(
            error_code="UNSUPPORTED_PACKAGE_MANAGER",
            host="",
            message=(
                f"package manager {package_manager!r} does not support "
                "mutation (install/remove/update); only dpkg/rpm/apk"
            ),
        )
    # operation вне install/remove/update — caller валидирует, но defensive.
    raise SshError(
        error_code="INVALID_OPERATION",
        host="",
        message=f"unsupported mutation operation: {operation}",
    )


async def _mutate_packages_impl(payload: dict, operation: str) -> dict:
    """Общая реализация install/remove/update под управляющей SSH-сессией.

    Заходит на сервер по ключу под управляющим пользователем (мутация —
    всегда managed-путь, server_service гейтит prepare), определяет package
    manager, валидирует имена пакетов и выполняет соответствующую команду
    под sudo. Возвращает `{server_id, operation, package_manager, packages,
    count, returncode}`.
    """
    server_id = payload["server_id"]
    raw_packages = payload.get("packages", [])
    account_id = payload.get("account_id")
    target_dept = payload.get("target_department_id")
    is_managed = bool(payload.get("is_managed"))

    creds = await resolve_ssh_creds(
        payload,
        server_id,
        account_id=account_id,
        target_dept=target_dept,
        is_managed=is_managed,
    )
    ssh_client.apply_session_hints(creds, payload)
    # На управляемом сервере подтянуть per-server управляющий ключ до сборки
    # сессии (build_session читает его из creds, а не из глобального env).
    await ssh_client.attach_management_creds(creds, server_id)
    host = creds.get("host") or creds.get("ssh_host") or server_id

    # update может идти без списка (обновить всё); install/remove обязаны
    # иметь хотя бы один пакет. Валидируем имена до подстановки в shell.
    packages: list[str]
    if operation == "update" and not raw_packages:
        packages = []
    else:
        packages = _validate_package_names(raw_packages, str(host))

    logger.info(
        "installed_packages.%s on %r packages=%s", operation, host, packages,
    )
    session = ssh_client.build_session(creds, server_id)
    try:
        await session.connect()
    except SshError as exc:
        if is_managed and exc.error_code in _CONNECT_FAILURE_CODES:
            await session.close()
            raise _remap_managed_connect_error(exc) from exc
        await session.close()
        raise
    async with session as ssh:
        package_manager = await _detect_package_manager(ssh)
        if package_manager not in _MUTABLE_PACKAGE_MANAGERS:
            raise SshError(
                error_code="UNSUPPORTED_PACKAGE_MANAGER",
                host=str(host),
                message=(
                    f"package manager {package_manager!r} on remote host does "
                    "not support mutation (install/remove/update); supported: "
                    "dpkg (apt-get) / rpm (dnf) / apk"
                ),
            )
        cmd = _build_mutation_command(package_manager, operation, packages)
        rc, stdout, stderr = await ssh.run(cmd, sudo=True)
        if rc != 0:
            raise SshError(
                error_code="PACKAGE_MUTATION_FAILED",
                host=str(host),
                cmd_sanitized=cmd,
                returncode=rc,
                stderr=(stderr or stdout).strip(),
                message=f"{package_manager} {operation} failed",
            )
    return {
        "server_id": server_id,
        "operation": operation,
        "package_manager": package_manager,
        "packages": packages,
        "count": len(packages),
        "returncode": rc,
    }


@broker.task("installed_packages.install")
async def installed_packages_install(task_id: str) -> None:
    """Установить пакеты на сервере (apt-get install / dnf install / apk add).

    Payload: `server_id`, `packages` (непустой список имён), management-поля
    (`is_managed`/`management_user`/`host`/`ssh_port`). Заходит под
    управляющим пользователем с sudo, обновляет индекс и ставит пакеты в
    не-интерактивном режиме.

    Возвращает `{server_id, operation: "install", package_manager, packages,
    count, returncode}`. Ошибки: `INVALID_PACKAGE_NAME`,
    `UNSUPPORTED_PACKAGE_MANAGER`, `PACKAGE_MUTATION_FAILED`,
    `SERVER_MANAGEMENT_AUTH_FAILED`, `NO_PACKAGE_MANAGER`.

    Связано: server_service `POST /servers/packages/bulk-action`,
    audit action `server.packages_install`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_packages_impl(payload, "install")

    await run_task(
        task_id,
        audit_action="server.packages_install",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=set(_MUTATION_AUDIT_SAFE_FIELDS),
    )


@broker.task("installed_packages.remove")
async def installed_packages_remove(task_id: str) -> None:
    """Удалить пакеты с сервера (apt-get remove / dnf remove / apk del).

    Payload и контракт — как у `installed_packages_install`, операция
    `remove`. Возвращает `{..., operation: "remove", ...}`.

    Связано: server_service `POST /servers/packages/bulk-action`,
    audit action `server.packages_remove`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_packages_impl(payload, "remove")

    await run_task(
        task_id,
        audit_action="server.packages_remove",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=set(_MUTATION_AUDIT_SAFE_FIELDS),
    )


@broker.task("installed_packages.update")
async def installed_packages_update(task_id: str) -> None:
    """Обновить пакеты на сервере (apt-get upgrade / dnf upgrade / apk upgrade).

    Payload: `server_id`, опциональный `packages` (если пуст — обновить всё),
    management-поля. Со списком пакетов обновляются только они
    (`--only-upgrade` у apt), без списка — все доступные обновления.

    Возвращает `{..., operation: "update", ...}` (`packages` пуст при
    обновлении всего).

    Связано: server_service `POST /servers/packages/bulk-action`,
    audit action `server.packages_update`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_packages_impl(payload, "update")

    await run_task(
        task_id,
        audit_action="server.packages_update",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=set(_MUTATION_AUDIT_SAFE_FIELDS),
    )
