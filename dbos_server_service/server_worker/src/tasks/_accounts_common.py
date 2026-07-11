"""Общий движок OS-учёток гостя ВМ поверх `TargetRunner`.

Заведение, обновление и снос учёток общего пула в госте ВМ раньше жили в трёх
местах и повторяли один и тот же набор shell-команд: create-флоу
(`vms._provision_guest_accounts`) и точечные таски `vms_accounts`
(`vm.account_update_on_host` / `vm.account_deprovision`). Команды —
`useradd`/`usermod`/`chpasswd`/`userdel` + запись `authorized_keys` — совпадали,
отличался только транспорт до гостя.

Здесь единый код поверх `GuestHopRunner` (примитив `run(cmd, *, sudo)`): все
guest-шаги идут вложенным `ssh` из hub-сессии, а вызывающая сторона больше не
дублирует шелл-строки. Тот же паттерн, что у пакетов
(`_packages_common.mutate_packages`) и инвентаризации (`_inventory_common`).

Расхождение с серверной стороной (`users.py` → `SshClient`) осознанное: там
прямая SSH-сессия с подачей пароля на stdin и managed-маркером ключа, здесь —
вложенный `ssh` одной строкой, поэтому пароль уходит инлайном
(`echo login:pwd | chpasswd`), а ключ — base64-обёрткой. Значения перед
подстановкой прогоняются по allow-list'ам (`validate_name`,
`_validate_guest_password`, `_PUBKEY_RE`), поэтому break-out из команды
невозможен.
"""

from __future__ import annotations

import base64
import logging
import re

from src.clients.ssh import SshError
from src.core.exceptions import CredentialFetchError
from src.services import server_service_client
from src.tasks._vms_helpers import validate_name

logger = logging.getLogger(__name__)

# Публичный SSH-ключ аккаунта: base64/PEM-безопасный набор символов. Сам ключ
# уходит в гостя base64-обёрнутым (несёт пробелы, расклеил бы вложенную
# guest-команду), но перед кодированием прогоняем по этому allow-list'у —
# defence-in-depth от подстановки постороннего мусора.
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/=@:.,_ \-]+$")


def _validate_guest_password(value: str, host: str) -> str:
    """Отбить пароль гостя с символами, ломающими inline-`chpasswd`.

    Пароль уходит в `bash -c 'echo <login>:<pwd> | chpasswd'` внутри вложенной
    guest-сессии; перевод строки/кавычки/подстановка расклеили бы команду.
    """
    if not isinstance(value, str) or not value:
        raise SshError(
            error_code="VM_INVALID_ARG", host=host,
            message="пароль гостя должен быть непустой строкой",
        )
    for bad in ("\n", "\r", "\0", "'", '"', "`", "$", ";"):
        if bad in value:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host,
                message="пароль гостя содержит недопустимый символ",
            )
    return value


def resolve_guest_groups(source: dict, host_label: str) -> list[str]:
    """Собрать unix-группы учётки: `unix_groups` + `sudo` при `has_sudo`.

    Каждое имя прогоняется через `validate_name` (тот же POSIX-набор, что и
    login). `sudo` доклеивается один раз, если аккаунт привилегированный и её
    ещё нет в списке. Порядок стабильный.
    """
    groups = [
        validate_name(str(g), host_label, "unix group")
        for g in (source.get("unix_groups") or [])
    ]
    if source.get("has_sudo") and "sudo" not in groups:
        groups.append("sudo")
    return groups


async def _guest_step(
    runner, cmd: str, error_code: str, message: str,
) -> tuple[int, str, str]:
    """Выполнить один guest-шаг под sudo; поднять `SshError` на non-zero rc.

    `runner.run` сам по себе не валидирует код возврата (в отличие от старого
    `run_hub_cmd`), поэтому проверку держим здесь — как это делает
    `_packages_common.mutate_packages`. `host` для ошибки берём из раннера (это
    hub, под которым падают guest-шаги).
    """
    rc, stdout, stderr = await runner.run(cmd, sudo=True)
    if rc != 0:
        raise SshError(
            error_code=error_code,
            host=runner.host,
            cmd_sanitized=cmd,
            returncode=rc,
            stderr=(stderr or stdout).strip(),
            message=message,
        )
    return rc, stdout, stderr


async def _fetch_guest_password(
    source_server_id, account_id, target_dept, login: str,
) -> str | None:
    """Best-effort пароль привязанной учётки через internal.

    По `server_id`+`account_id` (`fetch_account_password`) или, когда исходный
    сервер неизвестен — по одному `account_id` (`fetch_account_password_by_id`).
    Заводить учётку важнее пароля: если пароль вытянуть не удалось (нет
    доступа/эндпоинта), возвращаем `None` — пользователя всё равно создадим, без
    `chpasswd`.
    """
    try:
        if source_server_id:
            creds = await server_service_client.fetch_account_password(
                str(source_server_id), str(account_id), target_dept,
            )
        else:
            creds = await server_service_client.fetch_account_password_by_id(
                str(account_id), target_dept,
            )
        return creds.get("password")
    except CredentialFetchError:
        logger.warning(
            "vm provision: пароль учётки %s недоступен — заводим без пароля",
            login,
        )
        return None


async def set_password(
    runner, login: str, password: str, *, host_label: str,
    error_code: str = "VM_CREATE_FAILED",
) -> None:
    """Задать пароль учётки в госте через inline-`chpasswd`.

    Пароль валидируется (`_validate_guest_password`) и подставляется в
    `bash -c 'echo <login>:<pwd> | chpasswd'`. Пустой пароль сюда попадать не
    должен — caller решает, звать ли шаг (у discovered-аккаунта пароля нет).
    """
    safe = _validate_guest_password(str(password), host_label)
    await _guest_step(
        runner,
        f"bash -c 'echo {login}:{safe} | chpasswd'",
        error_code,
        f"не удалось задать пароль пользователю {login}",
    )


async def _apply_guest_key(
    runner, login: str, public_key: str, *, host_label: str,
    error_code: str = "VM_CREATE_FAILED",
) -> None:
    """Положить публичный ключ в `~<login>/.ssh/authorized_keys` гостя.

    Ключ несёт пробелы — заливаем base64-обёрткой, чтобы не расклеить вложенную
    guest-команду. `umask 077` + `chown` фиксируют права каталога/файла.
    """
    if not _PUBKEY_RE.fullmatch(str(public_key)):
        raise SshError(
            error_code="VM_INVALID_ARG", host=host_label,
            message=f"публичный ключ {login} содержит недопустимые символы",
        )
    b64 = base64.b64encode(str(public_key).encode()).decode()
    await _guest_step(
        runner,
        (
            f"bash -c 'umask 077 && mkdir -p ~{login}/.ssh && "
            f"echo {b64} | base64 -d >> ~{login}/.ssh/authorized_keys && "
            f"chown -R {login}: ~{login}/.ssh'"
        ),
        error_code,
        f"не удалось положить ключ пользователю {login}",
    )


async def provision_account(
    runner, acc: dict, *, host_label: str, target_dept: str | None,
) -> str | None:
    """Завести одну привязанную к ВМ учётку в госте (useradd + группы/пароль/ключ).

    `acc` — dict привязанного `server_account`: `{login, password?,
    ssh_public_key?, has_sudo?, unix_groups?, account_id?/id?, server_id?}`.
    Пароль берём из `password` (если server_service положил его в dispatch),
    иначе тянем best-effort через internal. useradd идемпотентен (id-guard),
    группы добиваем `usermod -aG`; их фейл критичен (`VM_CREATE_FAILED`).

    Не-dict элемент пропускается (возвращает `None`); иначе возвращает
    заведённый login.
    """
    if not isinstance(acc, dict):
        return None
    login = validate_name(str(acc.get("login") or ""), host_label, "account login")
    password = acc.get("password")
    account_id = acc.get("account_id") or acc.get("id")
    if password is None and account_id:
        password = await _fetch_guest_password(
            acc.get("server_id"), account_id, target_dept, login,
        )

    groups = resolve_guest_groups(acc, host_label)
    gopt = f" -G {','.join(groups)}" if groups else ""
    await _guest_step(
        runner,
        f"bash -c 'id {login} >/dev/null 2>&1 || useradd -m{gopt} {login}'",
        "VM_CREATE_FAILED",
        f"не удалось завести пользователя {login} в госте",
    )
    if groups:
        await _guest_step(
            runner,
            f"usermod -aG {','.join(groups)} {login}",
            "VM_CREATE_FAILED",
            f"не удалось добавить группы пользователю {login}",
        )
    if password:
        await set_password(runner, login, password, host_label=host_label)
    public_key = acc.get("ssh_public_key")
    if public_key:
        await _apply_guest_key(runner, login, public_key, host_label=host_label)
    return login


async def update_account_on_host(
    runner, login: str, groups: list[str], *,
    error_code: str = "VM_UPDATE_FAILED",
) -> None:
    """Синхронизировать группы/sudo учётки в госте (`usermod -aG`, аддитивно).

    Аддитивно к текущим группам — как серверный `account.update_on_host`.
    Пустой список групп — no-op (пароль здесь не трогаем).
    """
    if groups:
        await _guest_step(
            runner,
            f"usermod -aG {','.join(groups)} {login}",
            error_code,
            f"не удалось обновить группы пользователю {login} в госте",
        )


async def deprovision_account(
    runner, login: str, *, remove_home: bool = False,
    error_code: str = "VM_DEPROVISION_FAILED",
) -> None:
    """Удалить учётку из гостя (`userdel`, опц. `-r`).

    Идемпотентно: если пользователя в госте нет — не падает (`id ... || true`).
    """
    flag = "-r " if remove_home else ""
    await _guest_step(
        runner,
        f"bash -c 'id {login} >/dev/null 2>&1 && userdel {flag}{login} || true'",
        error_code,
        f"не удалось удалить пользователя {login} в госте",
    )
