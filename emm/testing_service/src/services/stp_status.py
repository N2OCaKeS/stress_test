"""Событийное обновление статуса СТП + ручной override (§6.2 плана миграции).

`sync_cell_from_queue_item()` вызывается из `services/queue.py` там же, где
уже обрабатываются терминальные переходы `queue_item` (`complete_item` на
успехе, `_fail_and_advance` на провале) — best-effort: находит связанную
`stp_cells` строку (если она есть — не у каждого запуска теста есть СТП-
прогон) и обновляет статус + `queue_item_id`. Попытка отправить статус в
Zephyr — тоже best-effort, сетевой сбой логируется и не пробрасывается
наружу, ровно как остальные внешние вызовы очереди (`acquire`/
`release-for-service`). При `verdict_source=zephyr` статус в Zephyr
выставил сам скрипт — сервис его не перезаписывает, только локальную ячейку.

Связь ищется по совпадению `(test.code == stp_test_case.code)` и
`(stand_id, RC, MODE, KERNEL)` самого свежего `stp_test_run` — тот же
join-ключ, что использует генерация (`services/stp.py`); если у item'а
задан `stp_test_run_id` — по нему. Из действий сценария в СТП пишет только
действие-вердикт, кейс — `scenarios.stp_test_case_code`
(`launch_stp.case_code_for_item`).

`PATCH /stp/cells/{id}` (ручной override, RBAC `admin`) — отдельный путь,
выставляет `status`+`updated_by`, НЕ трогает Zephyr (легаси-паттерн: ручная
правка локальна).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    Action, EntityType, QueueItemState, QueueVerdict, StpCellStatus, VerdictSource,
)
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import QueueItem, StpCell
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as stand_repo
from src.services import audit_service, launch_stp, permissions, secret_client, zephyr_client

logger = logging.getLogger(__name__)

_TERMINAL_STATUS_MAP: dict[str, str] = {
    QueueItemState.RUNNING: StpCellStatus.IN_PROGRESS,
    QueueItemState.SUCCEEDED: StpCellStatus.PASSED,
    QueueItemState.FAILED: StpCellStatus.FAIL,
    # Таймаут SSH-команды — тот же провал теста, что и generic `failed`,
    # для СТП/Zephyr разницы в причине нет.
    QueueItemState.TIMED_OUT: StpCellStatus.FAIL,
}


async def sync_cell_from_queue_item(db: AsyncSession, item: QueueItem) -> None:
    """Best-effort: если у `item` есть связанная `stp_cells` строка — обновить статус.

    No-op тихо, если СТП-прогон/ячейка для этого теста/стенда/RC не заведены —
    это обычный случай (не каждый тест участвует в СТП).
    """
    if item.debug_mode or item.failed_step == "launch_guard":
        return
    # Исход не определён (запуск без прогона в Zephyr п. 3а) — в СТП
    # писать нечего.
    if item.verdict == QueueVerdict.UNKNOWN:
        return
    new_status = _TERMINAL_STATUS_MAP.get(item.state)
    if new_status is None:
        return

    ctx = item.launch_context or {}
    rc, kernel, mode = ctx.get("RC"), ctx.get("KERNEL"), ctx.get("MODE")
    if not (rc and kernel and mode):
        return

    try:
        test = await test_definition_repo.get_by_id(db, item.test_id)
        if test is None:
            return
        case_code = await launch_stp.case_code_for_item(db, item, test.code)
        if case_code is None:
            return
        case = await stp_test_case_repo.get_by_code(db, case_code)
        if case is None:
            return
        run = await stp_test_run_repo.get_by_id(db, item.stp_test_run_id) if item.stp_test_run_id else await stp_test_run_repo.find_latest_for_context(
            db, stand_id=item.stand_id, os_version_id=rc, mode=mode, kernel=kernel,
        )
        if run is None:
            return
        cell = await stp_cell_repo.get_by_case_and_run(
            db, stp_test_case_id=case.id, stp_test_run_id=run.id,
        )
        if cell is None:
            return

        await stp_cell_repo.update(db, cell, {
            "status": new_status, "queue_item_id": item.id, "updated_by": None,
        })
        await db.commit()
        audit_service.emit(
            "stp_cell.auto_updated",
            target_id=cell.id, target_type="stp_cell",
            status="success", allowed=True,
            details={"status": new_status, "queue_item_id": item.id, "stp_test_run_id": run.id},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, не должно ронять queue.py
        logger.warning(
            "stp_status.sync_cell_from_queue_item failed for queue_item %s: %s", item.id, exc,
        )
        return

    if not run.zephyr_test_run_key or not case.zephyr_id:
        return
    # Вердикт прочитан из Zephyr — статус там выставил сам скрипт;
    # запись поверх него перетёрла бы вердикт теста (например, T3-таймаут
    # поверх статуса, опубликованного позже).
    if item.verdict_source == VerdictSource.ZEPHYR:
        return

    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        return
    settings = await dis_repo.get_by_department(db, stand.department_id)
    if settings is None or not settings.credential_id or not settings.jira_base_url:
        return
    try:
        _login, bearer_token = await secret_client.reveal_credential(settings.credential_id)
        if not bearer_token:
            return
        await zephyr_client.update_test_result(
            base_url=settings.jira_base_url,
            bearer_token=bearer_token,
            test_run_key=run.zephyr_test_run_key,
            test_case_key=case.zephyr_id,
            status=new_status,
        )
    except AppException as exc:
        logger.warning(
            "stp_status: failed to push status to Zephyr for cell of queue_item %s: %s",
            item.id, exc.message,
        )


async def manual_override(
    db: AsyncSession, identity: Identity, cell_id: str, status: str,
) -> StpCell:
    """`PATCH /stp/cells/{id}` — ручной override. RBAC `(stp_cell, *, update)`. Не трогает Zephyr."""
    try:
        await permissions.require_action(db, identity, EntityType.STP_CELL, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "stp_cell.manual_override",
            target_id=cell_id, target_type="stp_cell",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    if status not in set(StpCellStatus):
        raise DomainValidationError(
            error_code="STP_CELL_STATUS_INVALID",
            message="status must be one of: " + ", ".join(sorted(StpCellStatus)),
        )

    cell = await stp_cell_repo.get_by_id(db, cell_id)
    if cell is None:
        raise NotFoundError(error_code="STP_CELL_NOT_FOUND", message="Stp cell not found")

    await stp_cell_repo.update(db, cell, {
        "status": status, "updated_by": identity.user_id, "queue_item_id": None,
    })
    await db.commit()
    await db.refresh(cell)
    audit_service.emit(
        "stp_cell.manual_override",
        target_id=cell.id, target_type="stp_cell",
        status="success", allowed=True,
        details={"status": status, "updated_by": identity.user_id},
    )
    return cell
