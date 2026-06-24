"""Общая dispatch-обвязка для server-target SSH-task'ов.

`inventory.sync`, `users.inventory`, `installed_packages.list` строят один и
тот же базовый payload (ключи маршрутизации + management-режим + резолвнутый
аккаунт), зовут `worker_client.dispatch_task_with_hit` с одинаковым набором
аргументов и одинаково обрабатывают ConflictError / ServiceUnavailableError →
audit-failure → raise, success → audit-success. Раньше эта обвязка была
скопирована в трёх местах — забытый `target_resource_id` тиражировался по всем
сразу. Здесь она в одном месте: `target_resource_id=resolved_account_id`
проставляется внутри, мимо него новый call-site не пройдёт.

Преамбула (permission → visibility → decommissioned) и account-резолв остаются
у каждого call-site'а: `_dispatch_for_server` (worker_dispatch.py) шарит её для
power.status, а `users.inventory` / `installed_packages.list` несут свои
inline-проверки. Сюда едет уже загруженный `server`, резолвнутый
`resolved_account_id` и пер-сайтовые отличия (task_kind/audit_action/доп-payload).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.dependencies.idempotency import read_idempotency_key
from src.services import audit_service, worker_client


def build_ssh_task_payload(
    *,
    server,
    resolved_account_id: str | None,
    extra_payload: dict | None = None,
) -> dict:
    """Базовый payload для SSH-сбора (inventory/installed_packages).

    Ключи `host`/`ssh_port` читает воркер в `ssh_client._extract_host` /
    `_extract_port`; без них fallback на `server_id` (UUID) сломал бы DNS-резолв
    на dev-стендах. `is_managed`/`management_user`: на подготовленном сервере
    worker заходит по ключу под управляющим пользователем, иначе под
    дефолтным/переданным аккаунтом по паролю.

    Для неуправляемого сервера `resolved_account_id` кладётся в `account_id` —
    worker по нему запросит пароль и зайдёт под аккаунтом, а не root'ом. На
    управляемом сервере резолвер вернул None и ключ не добавляется.

    `extra_payload` мерджится поверх базы — для task-kind'ов с собственными
    полями (`pattern`/`max_rows` у installed_packages).
    """
    payload: dict = {
        "server_id": server.id,
        "target_department_id": server.department_id,
        "host": server.hostname,
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
    }
    if resolved_account_id is not None:
        payload["account_id"] = resolved_account_id
    if extra_payload:
        payload.update(extra_payload)
    return payload


async def dispatch_server_ssh_task(
    *,
    db: AsyncSession,
    identity,
    request,
    server,
    task_kind: str,
    audit_action: str,
    resolved_account_id: str | None,
    extra_payload: dict | None = None,
    success_extra_details: dict | None = None,
    priority: int = worker_client.TASK_PRIORITY_NORMAL,
) -> tuple[str, bool]:
    """Поставить server-target SSH-task в очередь worker'а и заэмитить audit.

    Инкапсулирует общую часть трёх dispatch-сайтов:

      * базовый payload (см. `build_ssh_task_payload`) + `extra_payload`;
      * `dispatch_task_with_hit(..., target_resource_id=resolved_account_id,
        idempotency_key=...)` — резолвнутый аккаунт пишется и в payload, и в
        колонку task-row, чтобы по строке задачи было видно учётку SSH-сессии;
      * commit;
      * ConflictError → audit-failure (`idempotent_conflict`) → raise;
      * ServiceUnavailableError → audit-failure (`worker_unreachable`) → raise;
      * success → audit-success.

    `success_extra_details` мерджится в details success-события (например
    `pattern` у installed_packages). `priority` пробрасывается в worker-row
    (дефолт normal); срочный фан-аут передаёт `TASK_PRIORITY_HIGH`. Возвращает
    `(task_id, idempotent_hit)`.
    """
    idempotency_key = read_idempotency_key(request)
    payload = build_ssh_task_payload(
        server=server,
        resolved_account_id=resolved_account_id,
        extra_payload=extra_payload,
    )
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server.id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            target_resource_id=resolved_account_id,
            idempotency_key=idempotency_key,
            priority=priority,
        )
        await db.commit()
    except ConflictError:
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    success_details = {
        "task_id": task_id,
        "task_kind": task_kind,
        "department_id": server.department_id,
        "idempotent_hit": idempotent_hit,
    }
    if success_extra_details:
        success_details.update(success_extra_details)
    audit_service.emit(
        audit_action, target_id=server.id, target_type="server",
        status="success", allowed=True,
        details=success_details,
    )
    return task_id, idempotent_hit
