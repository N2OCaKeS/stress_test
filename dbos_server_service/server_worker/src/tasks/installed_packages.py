"""Live-список установленных пакетов сервера через SSH (dpkg-query / rpm -qa).

В отличие от `inventory.sync` (full snapshot — kernel/cpu/disks/os/packages),
эта таска точечная: получаем только пакеты по glob-pattern и возвращаем
плоский список `{packages: [{name, version}, ...]}`. server_service её
вызывает из `GET /servers/{id}/installed-packages?pattern=...` и НЕ
сохраняет результат в БД — каждый запрос идёт live.

Логика SSH-команды:

* Если на хосте есть `dpkg` — Debian/Ubuntu/Astra Linux → `dpkg-query
  -W -f='${Package} ${Version}\\n' '<pattern>'`. Пустой output =
  ничего не подходит под pattern (rc=0 в dpkg-query).
* Иначе если есть `rpm` — RHEL/CentOS/Fedora → `rpm -qa --queryformat
  '%{NAME} %{VERSION}\\n' '<pattern>'`.
* Иначе — `SshError(error_code="NO_PACKAGE_MANAGER")`.

Pattern — shell glob (`htop`, `linux-image*`). asyncssh.run запускает
команду через `/bin/sh -c`, поэтому pattern оборачивается в одиночные
кавычки. Перед подстановкой worker сам валидирует его по allow-list'у
`[A-Za-z0-9._\\-+*?\\[\\]]+` (`_PATTERN_RE`) — это defence-in-depth, не
полагаемся на то, что server_service отсёк `'`/`$`/`;` и прочие
метасимволы, через которые можно вырваться из кавычек.
"""

from __future__ import annotations

import logging
import re

from src.clients.ssh import SshClient, SshError
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Только server_id, pattern, count — никаких имён пакетов в audit-details
# (могут раздуть log при большом результате и засветить версии CVE-relevant
# софта). Полный список лежит в task.result, оператор увидит его через
# API. То же решение, что в `tasks/inventory.py::AUDIT_SAFE_FIELDS`.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "pattern", "count", "package_manager"}

# Локальный allow-list символов для glob-pattern перед подстановкой в
# shell-команду. Принимаем только то, что нужно dpkg-query/rpm glob'у:
# буквы, цифры, точка, подчёркивание, дефис, плюс и сами glob-метасимволы
# `* ? [ ]`. Кавычка `'`, `$`, `;`, `\`, backtick, пробелы, перевод строки
# не проходят — это закрывает break-out из одиночных кавычек и shell-
# инъекцию. defence-in-depth: не полагаемся на валидацию server_service.
# Тот же класс символов заявлен в docstring модуля и в server_service.
_PATTERN_RE = re.compile(r"^[A-Za-z0-9._\-+*?\[\]]+$")


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
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host="",
        message=f"unsupported package manager: {package_manager}",
    )


def _parse_packages(stdout: str) -> list[dict[str, str]]:
    """Разобрать вывод dpkg-query / rpm -qa в список `[{name, version}, ...]`.

    Формат строки — `<name> <version>` (две колонки, разделитель пробел).
    Один пакет может встретиться несколько раз (например, разные версии
    `linux-image-*`) — отдаём как есть, без дедупликации: server_service /
    UI решает, как показывать (свернуть в array или поднять conflict).

    Пустая строка / строка без пробела — пропускаем (защита от мусора).
    """
    packages: list[dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
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


async def _detect_package_manager(ssh: SshClient) -> str:
    """Определить package manager на удалённом хосте.

    `command -v` возвращает rc=0 и путь если бинарь есть в PATH, rc!=0
    если нет. Используем именно `command -v`, а не `which`: POSIX-стандарт,
    есть и в Debian, и в RHEL без отдельной установки.

    Порядок проверки: dpkg → rpm. Если есть оба (теоретически возможно
    на гибридных хостах с alien) — берём dpkg, он первый по приоритету
    для Astra Linux target'а.
    """
    rc, _, _ = await ssh.run("command -v dpkg-query")
    if rc == 0:
        return "dpkg"
    rc, _, _ = await ssh.run("command -v rpm")
    if rc == 0:
        return "rpm"
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host=ssh.host,
        message="neither dpkg-query nor rpm found on remote host",
    )


@broker.task("installed_packages.list")
async def installed_packages_list(task_id: str) -> None:
    """Получить список установленных пакетов по glob-pattern.

    Что делает: подключается по SSH (через дефолтный root либо account_id
    из payload, если задан), определяет package manager (`dpkg`/`rpm`),
    выполняет `dpkg-query`/`rpm -qa` с pattern'ом, возвращает плоский
    список `{name, version}`-словарей.

    Параметры: `task_id`. Payload — `server_id` (обязательно), `pattern`
    (default `*`), опционально `account_id`, `ssh_login`, `ssh_host`,
    `target_department_id`.

    Возвращает: `{server_id, pattern, package_manager, count, packages: [...]}`.
    audit-detail'и режутся до AUDIT_SAFE_FIELDS — список пакетов наружу
    в loging_service не уходит.

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
        # аккаунта не нужен (и его может не быть у discovered-аккаунта).
        # Тянем пароль только если сессия реально пойдёт под самим аккаунтом
        # (тот же паттерн, что в inventory.sync / users.inventory).
        if account_id and not is_managed:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        else:
            creds = {"login": payload.get("ssh_login", "root")}

        # ssh_host из payload — server_service кладёт туда server.ip_address.
        if "ssh_host" in payload and "host" not in creds:
            creds["host"] = payload["ssh_host"]

        # is_managed/management_user из payload влияют на выбор сессии —
        # пропускаем их через apply_session_hints.
        ssh_client.apply_session_hints(creds, payload)

        host = creds.get("host") or creds.get("ssh_host") or server_id
        logger.info("installed_packages.list on %s pattern=%s", host, pattern)
        async with ssh_client.build_session(creds, server_id) as ssh:
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

        packages = _parse_packages(stdout)
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
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
