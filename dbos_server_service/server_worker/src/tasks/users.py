"""Инвентаризация OS-пользователей сервера по SSH.

Параллель `inventory.sync`, но цель — пользователи, а не железо. Поток:

  1. если в payload есть `account_id` — тянем пароль аккаунта через
     server_service, иначе дефолтный `ssh_login` (`root`);
  2. `ssh_client.collect_os_users` снимает `getent passwd` / `getent group` /
     `/etc/login.defs`;
  3. `os_users_facts_to_payload` фильтрует системных по `UID_MIN` и собирает
     flat-список под `UsersInventoryCallbackRequest`;
  4. `submit_users_inventory` POST'ит его обратно — server_service reconcile'ит.

Submit-фейл (network / 4xx / 5xx) НЕ роняет task'у в FAILED: список юзеров
остаётся в `task.result`, а submit-fail идёт в audit как
`submit_status=submit_failed:<code>`.
"""

import logging

from src.core.exceptions import CredentialFetchError
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit details.result. Сами логины/группы/home — потенциально
# чувствительный inventory, в audit кладём только счётчики и server_id.
# Полный список доступен админу через `Task.result`.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "user_count", "submit_status"}


@broker.task("users.inventory")
async def users_inventory(task_id: str) -> None:
    """Снять список OS-пользователей сервера по SSH и сдать в server_service.

    Параметры: `task_id`. Payload — `server_id`, опционально `account_id`,
    `ssh_login`, `target_department_id`.

    Возвращает: `{server_id, user_count, submit_status}` — в audit уходят
    только эти поля (см. AUDIT_SAFE_FIELDS), сам список юзеров — нет.

    Возможные ошибки: `CredentialFetchError` (если `account_id` задан, но
    server_service не отдал пароль), ошибки SSH-клиента.

    Связано с: `server_account.users_inventory` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload.get("account_id")
        target_dept = payload.get("target_department_id")

        if account_id:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        else:
            creds = {"login": payload.get("ssh_login", "root")}

        facts = await ssh_client.collect_os_users(creds, server_id)
        users_payload = ssh_client.os_users_facts_to_payload(facts)
        user_count = len(users_payload.get("users", []))

        submit_status: str
        try:
            await server_service_client.submit_users_inventory(
                server_id, users_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "users inventory submit failed server_id=%s error_code=%s; "
                "result kept in task.result",
                server_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        return {
            "server_id": server_id,
            "users": users_payload.get("users", []),
            "user_count": user_count,
            "submit_status": submit_status,
        }

    await run_task(
        task_id,
        audit_action="server_account.users_inventory",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


# Whitelist для audit details.result у provision/update/deprovision. Login и
# server_id — не секрет; пароль и состав групп в audit не уходят.
AUDIT_SAFE_FIELDS_PROVISION: set[str] = {
    "server_id", "account_id", "operation", "present_on_server",
}


@broker.task("account.provision")
async def account_provision(task_id: str) -> None:
    """Завести OS-пользователя на сервере (`useradd`) и подтвердить статус.

    Поток: `fetch_account_password` (login + расшифрованный общий пароль) →
    `ssh_client.provision_user` (useradd + chpasswd, groups/sudo/shell/home из
    payload) → `submit_provision_status(present=True)`.

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    `has_sudo`, `unix_groups`, `shell`, `home_dir`, опц. `target_department_id`.

    Idempotent: уже существующий пользователь синхронизируется, не падает.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.provision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
        await ssh_client.provision_user(
            creds, server_id,
            login=creds["login"],
            new_password=creds.get("password"),
            groups=payload.get("unix_groups") or [],
            has_sudo=bool(payload.get("has_sudo")),
            shell=payload.get("shell"),
            home_dir=payload.get("home_dir"),
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "provision", True, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "provision",
            "present_on_server": True,
        }

    await run_task(
        task_id,
        audit_action="server_account.provision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.update_on_host")
async def account_update_on_host(task_id: str) -> None:
    """Синхронизировать атрибуты OS-пользователя на сервере (`usermod`).

    Поток: `fetch_account_password` (нужен login + management-сессия) →
    `ssh_client.modify_user` (usermod groups/sudo/shell) →
    `submit_provision_status(present=True)`. Пароль не меняется.

    Параметры/payload — как у `account_provision`.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.update_on_host` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
        await ssh_client.modify_user(
            creds, server_id,
            login=creds["login"],
            groups=payload.get("unix_groups") or [],
            has_sudo=bool(payload.get("has_sudo")),
            shell=payload.get("shell"),
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "update", True, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "update",
            "present_on_server": True,
        }

    await run_task(
        task_id,
        audit_action="server_account.update_on_host",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.deprovision")
async def account_deprovision(task_id: str) -> None:
    """Удалить OS-пользователя с сервера (`userdel`) и подтвердить статус.

    Поток: `fetch_account_password` (нужен login + management-сессия) →
    `ssh_client.delete_user` (userdel, опц. --remove) →
    `submit_provision_status(present=False)`.

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    опц. `remove_home`, `target_department_id`.

    Idempotent: отсутствующий пользователь — не ошибка.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.deprovision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")
        remove_home = bool(payload.get("remove_home"))

        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
        await ssh_client.delete_user(
            creds, server_id, login=creds["login"], remove_home=remove_home,
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "deprovision", False, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "deprovision",
            "present_on_server": False,
        }

    await run_task(
        task_id,
        audit_action="server_account.deprovision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )
