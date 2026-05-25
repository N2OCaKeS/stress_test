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

Pattern — shell glob (`htop`, `linux-image*`), валидация на стороне
server_service'а (regex `[A-Za-z0-9._\\-+*?\\[\\]]+`). asyncssh.run
запускает команду через `/bin/sh -c`, поэтому pattern мы оборачиваем
в одиночные кавычки и проверяем, что в нём нет `'`, иначе break-out
из кавычек.
"""

from __future__ import annotations

import logging

from src.clients.ssh import SshClient, SshError
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Только server_id, pattern, count — никаких имён пакетов в audit-details
# (могут раздуть log при большом результате и засветить версии CVE-relevant
# софта). Полный список лежит в task.result, оператор увидит его через
# API. То же решение, что в `tasks/inventory.py::AUDIT_SAFE_FIELDS`.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "pattern", "count", "package_manager"}


def _build_command(package_manager: str, pattern: str) -> str:
    """Собрать shell-команду под выбранный package manager.

    Pattern оборачивается в одинарные кавычки — внутри не должно быть `'`,
    это уже отвалидировано на server_service'е (allow-list символов).
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

        if account_id:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        else:
            # Дефолтный fallback — root без пароля (для dev/test stand'ов,
            # где worker ходит по ключу через k8s-secret). Совместимо с
            # `inventory.sync`-flow.
            creds = {"login": payload.get("ssh_login", "root")}

        # ssh_host из payload — server_service кладёт туда `server.ip_address`,
        # `_extract_host` в фасаде берёт из `host`/`ssh_host`/server_id.
        if "ssh_host" in payload and "host" not in creds:
            creds["host"] = payload["ssh_host"]

        host = creds.get("host") or creds.get("ssh_host") or server_id
        username = creds.get("login") or creds.get("username") or "root"
        password = creds.get("password")
        port = int(creds.get("port") or creds.get("ssh_port") or 22)
        known_hosts = creds.get("known_hosts")

        logger.info("installed_packages.list on %s pattern=%s", host, pattern)
        async with SshClient(
            host=host,
            username=username,
            password=password,
            port=port,
            known_hosts=known_hosts,
        ) as ssh:
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
