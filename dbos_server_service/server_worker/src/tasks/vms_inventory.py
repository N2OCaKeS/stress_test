"""VM-аналоги `inventory.sync` и `users.inventory` — сбор через гостя ВМ.

ВМ наследует серверные функции инвентаризации: тот же набор команд
(`_inventory_common.collect_inventory` / `collect_os_users`, зеркало
`SshClient.get_inventory`/`get_os_users`) и тот же разбор facts
(`ssh_client.inventory_facts_to_payload` / `os_users_facts_to_payload`), что и на
прямом сервере. Отличается только транспорт: команды едут в гостя вложенным ssh
из hub-сессии (`GuestHopRunner`), а не по прямой SSH-сессии.

Инвентарь managed-ВМ снимается ПО УПРАВЛЯЮЩЕМУ КЛЮЧУ (базовая учётка образа
`u` снесена на prepare), поэтому ключ тянется `load_guest_key` и шредится в
`finally`; legacy/unmanaged (ключа в payload нет) — фолбэк на базовую учётку
`u`/`1`, как в `vm.list_packages`. Результат сдаётся обратно в server_service
internal-callback'ом (`submit_vm_inventory_facts` / `submit_vm_users_inventory`).
Submit-фейл (endpoint недоступен / 4xx / 5xx) НЕ роняет task'у в FAILED —
facts/юзеры остаются в `task.result`, а submit-fail идёт в audit как
`submit_status=submit_failed:<code>`.
"""

from __future__ import annotations

import logging

from src.core.exceptions import CredentialFetchError
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._inventory_common import collect_inventory, collect_os_users
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

# Whitelist для audit details.result. Как на сервере (inventory.sync /
# users.inventory): сырой inventory и список юзеров в audit не уходят — только
# сам факт и счётчики. Полный результат виден админу через `Task.result`.
AUDIT_SAFE_FIELDS_INVENTORY: set[str] = {"vm_id", "vm_name", "submit_status"}
AUDIT_SAFE_FIELDS_USERS: set[str] = {
    "vm_id", "vm_name", "user_count", "submit_status",
}


def _host_label(payload: dict) -> str:
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


@broker.task("vm.inventory_sync")
async def vm_inventory_sync(task_id: str) -> None:
    """Снять hardware-inventory гостя ВМ по SSH через hub и сдать в server_service.

    Что делает: открывает управляющую сессию к hub'у, резолвит IP гостя
    (`guest_ip`/`ip_address` из payload либо `virsh domifaddr`), заходит в гостя
    по управляющему ключу (managed-ВМ) либо по базовой учётке `u`/`1` (legacy),
    общим `collect_inventory` снимает hostname/kernel/cpu/disks/os/pci, мапит их
    в flat-payload (`inventory_facts_to_payload`) и сдаёт callback'ом
    `submit_vm_inventory_facts`. Ключ шредит в `finally`.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, hub-блок,
    `guest_ip`/`ip_address`, опц. `creds_stash_key`, `target_department_id`.

    Возвращает: `{vm_id, vm_name, facts, submit_status}` — в audit уходят только
    `vm_id`/`vm_name`/`submit_status` (см. AUDIT_SAFE_FIELDS_INVENTORY).

    Связано с: `vm.inventory_sync` audit action.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )

        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            runner = GuestHopRunner(ssh, connect, host=host)
            try:
                facts = await collect_inventory(runner)
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)

        inventory_payload = ssh_client.inventory_facts_to_payload(facts)
        submit_status: str
        try:
            await server_service_client.submit_vm_inventory_facts(
                vm_id, inventory_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "vm inventory submit failed vm_id=%s error_code=%s; "
                "facts kept in task.result",
                vm_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "facts": facts,
            "submit_status": submit_status,
        }

    await run_task(
        task_id,
        audit_action="vm.inventory_sync",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_INVENTORY,
    )


@broker.task("vm.users_inventory")
async def vm_users_inventory(task_id: str) -> None:
    """Снять список OS-пользователей гостя ВМ по SSH через hub и сдать в server_service.

    Параллель `vm.inventory_sync`, но цель — пользователи: `collect_os_users`
    снимает `getent passwd`/`getent group`/`/etc/login.defs`,
    `os_users_facts_to_payload` фильтрует системных по `UID_MIN` и собирает
    flat-список, `submit_vm_users_inventory` сдаёт его в server_service, где идёт
    reconcile привязанных к ВМ учёток (warn-on-drift, БД-истина не перетирается).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, hub-блок,
    `guest_ip`/`ip_address`, опц. `creds_stash_key`, `target_department_id`.

    Возвращает: `{vm_id, vm_name, users, user_count, submit_status}` (+ `diffs`/
    `unknown_users`/`reconcile_summary` при успешном submit) — в audit уходят
    только счётчики (см. AUDIT_SAFE_FIELDS_USERS).

    Связано с: `vm.users_inventory` audit action.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )

        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            runner = GuestHopRunner(ssh, connect, host=host)
            try:
                facts = await collect_os_users(runner)
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)

        users_payload = ssh_client.os_users_facts_to_payload(facts)
        user_count = len(users_payload.get("users", []))

        submit_status: str
        reconcile: dict | None = None
        try:
            reconcile = await server_service_client.submit_vm_users_inventory(
                vm_id, users_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "vm users inventory submit failed vm_id=%s error_code=%s; "
                "result kept in task.result",
                vm_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        result = {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "users": users_payload.get("users", []),
            "user_count": user_count,
            "submit_status": submit_status,
        }
        if isinstance(reconcile, dict):
            result["diffs"] = reconcile.get("diffs", [])
            result["unknown_users"] = reconcile.get("unknown_users", [])
            result["reconcile_summary"] = {
                "created": reconcile.get("created"),
                "present": reconcile.get("present"),
                "drifted": reconcile.get("drifted"),
            }
        return result

    await run_task(
        task_id,
        audit_action="vm.users_inventory",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_USERS,
    )
