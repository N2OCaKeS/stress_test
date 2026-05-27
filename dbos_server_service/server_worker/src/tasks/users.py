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
