"""Авто-обновление inventory + power-статуса подготовленных серверов.

Два триггера, одна механика:

* после prepare (`record_server_prepared`) — сразу освежаем один сервер, чтобы
  оператор не жал inventory/power вручную;
* по расписанию (`auto_inventory.sweep` в worker-scheduler'е) — проходим по
  всем `is_managed` серверам платформы.

Для каждого сервера ставим две задачи через штатный `worker_client`:

* `inventory.sync` — SSH-сбор фактов под управляющим ключом (сервер уже
  prepared, ключ есть);
* `power.status` — живая проба питания. Воркер сам выбирает источник
  (bmc → ping → ssh); для серверов без IPMI состояние определяется по сетевой
  достижимости, а не остаётся `unknown`.

Всё best-effort: недоступность воркера / idempotent-conflict на одной задаче
не валит остальные и не валит вызвавший prepare-callback. Аудит — существующими
действиями `server.inventory_sync` / `server.power_status` с пометкой источника
(`source=auto_prepared` / `auto_scheduled`), чтобы SIEM отличал авто-прогон от
ручного диспатча.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import BusyState, ServerStatus
from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.repositories import server as server_repo
from src.services import audit_service, worker_client

logger = logging.getLogger(__name__)

# Триггеры авто-обновления — идут в `details.source` каждого audit-события.
SOURCE_PREPARED = "auto_prepared"
SOURCE_SCHEDULED = "auto_scheduled"
# Частый power-sweep (ping/ssh/ipmi по всем серверам) — свой маркер источника.
SOURCE_POWER_SWEEP = "auto_power_sweep"

# Одиночная пара для power-only прогона (без inventory.sync).
_POWER_TASK: tuple[str, str] = ("power.status", "server.power_status")

# Пары (task_kind, audit_action) авто-обновления. inventory.sync — SSH-сбор,
# power.status — живая проба питания c ping/ssh fallback'ом на стороне воркера.
_AUTO_TASKS: tuple[tuple[str, str], ...] = (
    ("inventory.sync", "server.inventory_sync"),
    ("power.status", "server.power_status"),
)


def _auto_payload(server) -> dict:
    """Базовый payload авто-задач: ключи маршрутизации + management-режим.

    Тот же набор, что строит `build_ssh_task_payload` в endpoint-слое
    (`host`/`ssh_port` — SSH-адресация; `is_managed`/`management_user` — вход
    под управляющим ключом). power.status использует только `host`/`ssh_port`
    для reachability-пробы, лишние ключи игнорирует. В host — IP, а не hostname:
    короткие имена не резолвятся из пода воркера, IP достижим без резолва.
    """
    return {
        "server_id": server.id,
        "target_department_id": server.department_id,
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
    }


async def _dispatch_one(
    db: AsyncSession,
    *,
    server,
    task_kind: str,
    audit_action: str,
    source: str,
    actor_id: str | None,
    request_id: str | None,
) -> str | None:
    """Поставить одну авто-задачу для сервера. Вернуть task_id либо None.

    Best-effort: ConflictError (idempotent-replay) и ServiceUnavailableError
    (воркер/redis недоступны) уводятся в failure-audit и возвращают None, а не
    пробрасываются — авто-обновление не должно валить ни prepare-callback, ни
    остальные серверы sweep'а. Любое иное исключение тоже глушим с warning'ом.
    """
    payload = _auto_payload(server)
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server.id,
            payload=payload,
            created_by=actor_id,
            request_id=request_id,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        reason = (
            "idempotent_conflict"
            if isinstance(exc, ConflictError)
            else "worker_unreachable"
        )
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "task_kind": task_kind,
                "source": source,
                "department_id": server.department_id,
            },
        )
        return None
    except Exception as exc:  # noqa: BLE001 — авто-обновление не должно падать
        await db.rollback()
        logger.warning(
            "auto_inventory dispatch failed server_id=%s task_kind=%s: %s",
            server.id, task_kind, type(exc).__name__,
        )
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "dispatch_error",
                "task_kind": task_kind,
                "source": source,
                "department_id": server.department_id,
            },
        )
        return None
    audit_service.emit(
        audit_action, target_id=server.id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "source": source,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return task_id


async def refresh_server(
    db: AsyncSession,
    server,
    *,
    source: str,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Освежить inventory + power-статус одного подготовленного сервера.

    Списанные серверы пропускаем (worker-операции им не адресуем). Сервер в
    `busy_state='updating'` тоже пропускаем: во время astra-update блокировка
    запрещает любые операции над боксом, а плановый SSH-inventory параллельно с
    apt/astra-update как раз то, что она должна исключать. Обе задачи независимы:
    фейл одной не мешает второй. Возвращает
    `{server_id, dispatched: {task_kind: task_id}, skipped: bool}`.
    """
    if server.status == ServerStatus.DECOMMISSIONED:
        return {"server_id": server.id, "dispatched": {}, "skipped": True}
    if server.busy_state == BusyState.UPDATING:
        return {"server_id": server.id, "dispatched": {}, "skipped": True}
    dispatched: dict[str, str] = {}
    for task_kind, audit_action in _AUTO_TASKS:
        task_id = await _dispatch_one(
            db,
            server=server,
            task_kind=task_kind,
            audit_action=audit_action,
            source=source,
            actor_id=actor_id,
            request_id=request_id,
        )
        if task_id is not None:
            dispatched[task_kind] = task_id
    return {"server_id": server.id, "dispatched": dispatched, "skipped": False}


async def probe_server_power(
    db: AsyncSession,
    server,
    *,
    source: str,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> str | None:
    """Поставить только живую пробу питания (`power.status`) для одного сервера.

    В отличие от `refresh_server`, inventory.sync НЕ ставим: проба ping/ssh/ipmi
    read-only и снимается для ЛЮБОГО сервера — неподготовленного, обновляющегося,
    без IPMI. Единственный skip — списанный сервер (worker-операции ему не шлём).
    Возвращает task_id либо None (skip / best-effort фейл диспатча).
    """
    if server.status == ServerStatus.DECOMMISSIONED:
        return None
    task_kind, audit_action = _POWER_TASK
    return await _dispatch_one(
        db,
        server=server,
        task_kind=task_kind,
        audit_action=audit_action,
        source=source,
        actor_id=actor_id,
        request_id=request_id,
    )


async def fanout_power_sweep(
    db: AsyncSession,
    *,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Частый прогон живой пробы питания по ВСЕМ не-списанным серверам.

    Отличие от `fanout_auto_inventory`: населённость — все активные серверы
    (`list_all_active`, без фильтра `is_managed`), а ставится только
    `power.status` — ping/ssh/ipmi. Так доступность и питание держатся
    актуальными для каждого сервера, включая неподготовленные и те, у кого нет
    IPMI (там ipmi_power_state=unknown, без шума). inventory.sync сюда не входит:
    его гоняет редкий `fanout_auto_inventory`.

    Cap `auto_inventory_fanout_max` тот же throttle против шторма; при
    превышении режем хвост и эмитим `power_sweep.truncated`. Возвращает
    `{total_servers, processed, dispatched_tasks, truncated}`.
    """
    cap = get_settings().auto_inventory_fanout_max
    servers = await server_repo.list_all_active(db, limit=cap)
    total_servers = await server_repo.count_all_active(db)
    truncated = max(0, total_servers - cap)
    if truncated:
        audit_service.emit(
            "power_sweep.truncated",
            target_id=None, target_type="server",
            status="warning", allowed=True,
            details={
                "total_servers": total_servers,
                "cap": cap,
                "truncated_count": truncated,
            },
        )

    processed = 0
    dispatched_tasks = 0
    for server in servers:
        task_id = await probe_server_power(
            db, server,
            source=SOURCE_POWER_SWEEP,
            actor_id=actor_id,
            request_id=request_id,
        )
        processed += 1
        if task_id is not None:
            dispatched_tasks += 1

    return {
        "total_servers": total_servers,
        "processed": processed,
        "dispatched_tasks": dispatched_tasks,
        "truncated": truncated,
    }


async def fanout_auto_inventory(
    db: AsyncSession,
    *,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Плановый прогон: inventory.sync + power.status по всем managed-серверам.

    Список берём из `server_repo.list_managed` (только `is_managed`,
    не decommissioned), capped по `auto_inventory_fanout_max` — cap работает как
    throttle против шторма задач на крупной платформе. При превышении режем
    хвост и эмитим `auto_inventory_sweep.truncated`; отрезанные серверы
    выровняются следующим прогоном.

    Возвращает сводку `{total_managed, processed, dispatched_tasks, truncated}`.
    """
    cap = get_settings().auto_inventory_fanout_max
    servers = await server_repo.list_managed(db, limit=cap)
    total_managed = await server_repo.count_managed(db)
    truncated = max(0, total_managed - cap)
    if truncated:
        audit_service.emit(
            "auto_inventory_sweep.truncated",
            target_id=None, target_type="server",
            status="warning", allowed=True,
            details={
                "total_managed": total_managed,
                "cap": cap,
                "truncated_count": truncated,
            },
        )

    processed = 0
    dispatched_tasks = 0
    for server in servers:
        result = await refresh_server(
            db, server,
            source=SOURCE_SCHEDULED,
            actor_id=actor_id,
            request_id=request_id,
        )
        if result["skipped"]:
            continue
        processed += 1
        dispatched_tasks += len(result["dispatched"])

    return {
        "total_managed": total_managed,
        "processed": processed,
        "dispatched_tasks": dispatched_tasks,
        "truncated": truncated,
    }
