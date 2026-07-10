"""Мутации пакетов гостя ВМ — install / remove / update по SSH через hub.

VM-аналог серверного `installed_packages.{install,remove,update}`: та же общая
логика мутации (`_packages_common.mutate_packages` — детект менеджера, валидация
имён, сборка не-интерактивной команды apt-get/dnf/apk и запуск под sudo), но
цель — гость ВМ. Команды едут вложенным ssh из hub-сессии (`GuestHopRunner`), а
не по прямой SSH-сессии.

Managed-ВМ мутируется ПО УПРАВЛЯЮЩЕМУ КЛЮЧУ (базовая учётка образа `u` снесена на
prepare), поэтому ключ тянется `load_guest_key` и шредится в `finally`;
legacy/unmanaged (ключа в payload нет) — фолбэк на базовую учётку `u`/`1`, как в
`vm.list_packages`. Результат/контракт зеркалят серверные мутации, чтобы UI
переиспользовал тот же submit/poll: `{vm_id, vm_name, operation, package_manager,
packages, count, returncode}`.
"""

from __future__ import annotations

import logging

from src.main import broker
from src.tasks import _packages_common as pkg
from src.tasks._runner import run_task
from src.tasks._target_runner import GuestHopRunner
from src.tasks._vm_prepare_helpers import (
    _shred_temp_key,
    choose_guest_connector,
    load_guest_key,
)
from src.tasks._vms_helpers import (
    open_hub_session,
    resolve_guest_ip,
    validate_name,
)

logger = logging.getLogger(__name__)

# Имена пакетов сами по себе не секрет, но это интент оператора (что ставит/
# сносит) — отдаём в audit явно, как серверные мутации. Полный stdout менеджера
# наружу не уходит — лежит в task.result.
AUDIT_SAFE_FIELDS_MUTATE: set[str] = {
    "vm_id", "vm_name", "operation", "package_manager", "packages", "count",
    "returncode",
}


def _host_label(payload: dict) -> str:
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


async def _mutate_impl(payload: dict, operation: str) -> dict:
    """Общая реализация install/remove/update на госте ВМ через hub.

    Открывает управляющую сессию к hub'у, резолвит IP гостя, заходит в гостя по
    управляющему ключу (managed) либо по базовой учётке `u`/`1` (legacy) и отдаёт
    саму мутацию общему `mutate_packages` через `GuestHopRunner`. Ключ шредит в
    `finally`.
    """
    vm_id = payload["vm_id"]
    host_label = _host_label(payload)
    vm_name = validate_name(
        payload.get("vm_name") or payload["name"], host_label, "vm_name",
    )
    raw_packages = payload.get("packages", [])
    os_family = str(payload.get("os_family") or "").strip().lower()

    logger.info("vm.%s_packages on %r via hub %r", operation, vm_name, host_label)
    session, host = await open_hub_session(payload)
    async with session as ssh:
        guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
        mgmt_user, key_path = await load_guest_key(ssh, host, payload)
        connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
        # Цель — гость ВМ: команды едут вложенным ssh из hub-сессии (hop-раннер);
        # sudo зашивается в connect-строку внутри гостя.
        runner = GuestHopRunner(ssh, connect, host=host)
        try:
            package_manager, packages, rc = await pkg.mutate_packages(
                runner, operation, raw_packages, os_family=os_family,
            )
        finally:
            if key_path:
                await _shred_temp_key(ssh, key_path)

    return {
        "vm_id": vm_id,
        "vm_name": vm_name,
        "operation": operation,
        "package_manager": package_manager,
        "packages": packages,
        "count": len(packages),
        "returncode": rc,
    }


@broker.task("vm.install_packages")
async def vm_install_packages(task_id: str) -> None:
    """Установить пакеты в госте ВМ (apt-get install / dnf install / apk add).

    Payload: `vm_id`, `vm_name`/`name`, `packages` (непустой список имён), hub-блок,
    `guest_ip`/`ip_address`, опц. `os_family`, `creds_stash_key`,
    `target_department_id`. Заходит в гостя под управляющим пользователем с sudo,
    обновляет индекс и ставит пакеты в не-интерактивном режиме.

    Возвращает `{vm_id, vm_name, operation: "install", package_manager, packages,
    count, returncode}`. Ошибки: `INVALID_PACKAGE_NAME`, `NO_PACKAGE_MANAGER`,
    `UNSUPPORTED_PACKAGE_MANAGER`, `PACKAGE_MUTATION_FAILED`, `VM_GUEST_NO_IP`.

    Связано: server_service `POST /vms/{id}/packages/action`,
    audit action `vm.packages_installed`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_impl(payload, "install")

    await run_task(
        task_id,
        audit_action="vm.packages_installed",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=set(AUDIT_SAFE_FIELDS_MUTATE),
    )


@broker.task("vm.remove_packages")
async def vm_remove_packages(task_id: str) -> None:
    """Удалить пакеты из гостя ВМ (apt-get remove / dnf remove / apk del).

    Payload и контракт — как у `vm_install_packages`, операция `remove`.
    Возвращает `{..., operation: "remove", ...}`.

    Связано: server_service `POST /vms/{id}/packages/action`,
    audit action `vm.packages_removed`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_impl(payload, "remove")

    await run_task(
        task_id,
        audit_action="vm.packages_removed",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=set(AUDIT_SAFE_FIELDS_MUTATE),
    )


@broker.task("vm.update_packages")
async def vm_update_packages(task_id: str) -> None:
    """Обновить пакеты в госте ВМ (apt-get upgrade / dnf upgrade / apk upgrade).

    Payload: `vm_id`, `vm_name`/`name`, опциональный `packages` (если пуст —
    обновить всё), hub-блок, `guest_ip`/`ip_address`, опц. `os_family`,
    `creds_stash_key`. Со списком пакетов обновляются только они
    (`--only-upgrade` у apt), без списка — все доступные обновления.

    Возвращает `{..., operation: "update", ...}` (`packages` пуст при обновлении
    всего).

    Связано: server_service `POST /vms/{id}/packages/action`,
    audit action `vm.packages_updated`.
    """
    async def _impl(payload: dict) -> dict:
        return await _mutate_impl(payload, "update")

    await run_task(
        task_id,
        audit_action="vm.packages_updated",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=set(AUDIT_SAFE_FIELDS_MUTATE),
    )
