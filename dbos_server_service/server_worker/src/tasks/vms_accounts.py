"""Задачи учёток общего пула в гостях ВМ — provision/update/deprovision.

Тот же `server_account`, что живёт на серверах, привязывается и к ВМ. Worker
заходит на hub-сервер ВМ под управляющей учёткой (ключевая сессия, sudo
NOPASSWD), оттуда по `sshpass` в гостя и заводит/меняет/сносит unix-учётку.

Provision переиспользует `_provision_guest_accounts` из `vms.py` (тот же
useradd + chpasswd + authorized_keys + группы, что и при `vm.create`): пароль
воркер тянет сам через internal (`fetch_account_password_by_id`), в payload
только публичные атрибуты. update/deprovision — точечные usermod/userdel в
госте по той же вложенной guest-сессии.
"""

from __future__ import annotations

from src.main import broker
from src.tasks._runner import run_task
from src.tasks._vm_prepare_helpers import (
    _shred_temp_key,
    choose_guest_connector,
    load_guest_key,
)
from src.tasks._vms_helpers import (
    open_hub_session,
    resolve_guest_ip,
    run_hub_cmd,
    validate_name,
)
from src.tasks.vms import _provision_guest_accounts

AUDIT_SAFE_FIELDS: set[str] = {"vm_id", "vm_name", "login", "present", "operation"}


def _host_label(payload: dict) -> str:
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


def _guest_groups(payload: dict, login: str, host_label: str) -> list[str]:
    """Собрать список unix-групп для usermod: `unix_groups` + `sudo` при has_sudo."""
    groups = [
        validate_name(str(g), host_label, "unix group")
        for g in (payload.get("unix_groups") or [])
    ]
    if payload.get("has_sudo") and "sudo" not in groups:
        groups.append("sudo")
    return groups


@broker.task("vm.account_provision")
async def vm_account_provision(task_id: str) -> None:
    """Завести учётку общего пула в госте ВМ (useradd + пароль/ключ/группы).

    Что делает: открывает управляющую сессию к hub'у, резолвит IP гостя
    (`guest_ip`/`ip_address` из payload либо `virsh domifaddr`), заводит учётку
    через `_provision_guest_accounts` (useradd идемпотентен; пароль воркер тянет
    через internal по `account_id`; публичный ключ и группы — из payload).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`, hub-блок, `guest_ip`,
    `login`, `account_id`, опц. `has_sudo`/`unix_groups`/`ssh_public_key`.

    Возвращает: `{vm_id, vm_name, login, present, provisioned}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        login = validate_name(str(payload.get("login") or ""), host_label, "account login")
        account = {
            "login": login,
            "account_id": payload.get("account_id"),
            "has_sudo": payload.get("has_sudo"),
            "unix_groups": payload.get("unix_groups") or [],
            "ssh_public_key": payload.get("ssh_public_key"),
        }
        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            try:
                provisioned = await _provision_guest_accounts(
                    ssh, host, guest_ip, [account], host_label, target_dept,
                    connect=connect,
                )
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "login": login,
            "present": True,
            "provisioned": provisioned,
        }

    await run_task(
        task_id,
        audit_action="vm.account_provision",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("vm.account_update_on_host")
async def vm_account_update_on_host(task_id: str) -> None:
    """Синхронизировать группы/sudo учётки в госте ВМ (usermod).

    Что делает: открывает сессию к hub'у, резолвит IP гостя, выполняет
    `usermod -aG` по группам аккаунта (+ `sudo` при has_sudo). Пароль не трогает.
    Аддитивно к текущим группам — как серверный `account.update_on_host`.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`, hub-блок, `guest_ip`,
    `login`, опц. `has_sudo`/`unix_groups`.

    Возвращает: `{vm_id, vm_name, login, present, operation}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        login = validate_name(str(payload.get("login") or ""), host_label, "account login")
        groups = _guest_groups(payload, login, host_label)
        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            try:
                if groups:
                    await run_hub_cmd(
                        ssh,
                        connect(
                            f"usermod -aG {','.join(groups)} {login}", sudo=True,
                        ),
                        host, "VM_UPDATE_FAILED",
                        f"не удалось обновить группы пользователю {login} в госте",
                    )
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "login": login,
            "present": True,
            "operation": "update",
        }

    await run_task(
        task_id,
        audit_action="vm.account_update_on_host",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("vm.account_deprovision")
async def vm_account_deprovision(task_id: str) -> None:
    """Удалить учётку из гостя ВМ (userdel).

    Что делает: открывает сессию к hub'у, резолвит IP гостя, выполняет
    `userdel` (опц. `--remove` по `remove_home`). Идемпотентно: если
    пользователя в госте нет — не падает.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`, hub-блок, `guest_ip`,
    `login`, опц. `remove_home`.

    Возвращает: `{vm_id, vm_name, login, present, operation}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        login = validate_name(str(payload.get("login") or ""), host_label, "account login")
        remove_home = bool(payload.get("remove_home"))
        flag = "-r " if remove_home else ""
        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            try:
                await run_hub_cmd(
                    ssh,
                    connect(
                        f"bash -c 'id {login} >/dev/null 2>&1 && userdel {flag}{login} || true'",
                        sudo=True,
                    ),
                    host, "VM_DEPROVISION_FAILED",
                    f"не удалось удалить пользователя {login} в госте",
                )
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "login": login,
            "present": False,
            "operation": "deprovision",
        }

    await run_task(
        task_id,
        audit_action="vm.account_deprovision",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
