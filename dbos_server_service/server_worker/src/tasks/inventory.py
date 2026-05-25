"""Inventory sync — снимает hostname/kernel/cpu/disks/os/pci через SSH.

SSH делается через `services.ssh_client.collect_inventory`, который под
капотом использует `clients.ssh.SshClient` (asyncssh). Сырой facts-dict
мапится в flat-payload через `ssh_client.inventory_facts_to_payload`
(парсит lscpu/lsblk JSON и os-release), затем отдаётся в server_service
через `submit_inventory_facts` — endpoint
`/internal/.../servers/{id}/inventory`.

Если submit упал (network / 4xx / 5xx) — мы НЕ роняем task'у в FAILED:
facts всё равно сохранены в `task.result`, а submit-fail идёт в audit
как `submit_status=submit_failed:<code>`. Это даёт оператору возможность
вытянуть inventory из task-row, даже если server_service временно не
доступен.
"""

import logging

from src.core.exceptions import CredentialFetchError
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit details.result.
#
# inventory.sync возвращает `{server_id, facts}`. `facts` намеренно
# исключены: содержат kernel-version, packages, disks — каждый по
# отдельности не «секрет», но (а) могут раскрыть CVE-relevant информацию
# в audit-логе, (б) объём раздувает loging_service-storage. Полный
# inventory доступен админу через `Task.result` (worker-DB), audit
# сторона видит только сам факт «inventory_sync для server_id».
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "submit_status"}


@broker.task("inventory.sync")
async def inventory_sync(task_id: str) -> None:
    """Собрать inventory сервера по SSH.

    Что делает: если в payload есть `account_id` — тянет пароль аккаунта
    через server_service, иначе использует дефолтный логин из
    `payload.ssh_login` (или `root`). Далее `ssh_client.collect_inventory`
    отдаёт OS/kernel/packages/disks. Результат — `{server_id, facts}`.

    Параметры: `task_id`. Payload — `server_id`, опционально `account_id`,
    `ssh_login`, `target_department_id`.

    Возвращает: `{server_id, facts}` — в audit уходит только `server_id`
    (см. AUDIT_SAFE_FIELDS).

    Возможные ошибки: `CredentialFetchError` (если `account_id` задан, но
    server_service не отдал пароль), ошибки SSH-клиента.

    Связано с: `server.inventory_sync` audit action.
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

        facts = await ssh_client.collect_inventory(creds, server_id)
        # Маппинг raw facts → flat schema для server_service. См.
        # `services/ssh_client.py::inventory_facts_to_payload`.
        inventory_payload = ssh_client.inventory_facts_to_payload(facts)
        # Best-effort callback в server_service. При фейле facts остаются
        # в task.result, оператор увидит warning в логах; task НЕ
        # помечается failed.
        submit_status: str
        try:
            await server_service_client.submit_inventory_facts(
                server_id, inventory_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "inventory submit failed server_id=%s error_code=%s; "
                "facts kept in task.result",
                server_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        return {"server_id": server_id, "facts": facts, "submit_status": submit_status}

    await run_task(
        task_id,
        audit_action="server.inventory_sync",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
