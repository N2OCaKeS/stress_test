"""Очередь прогонов теста на стенде (§2.4, §5, §5.5 плана миграции).

Один цикл — от постановки в очередь до освобождения стенда — устроен так:

1. `enqueue()` кладёт `queue_item` в очередь стенда. Если это первый активный
   элемент очереди (стенд был свободен от работы testing_service) — сразу
   запускает цикл подготовки: `acquire-for-service` + `prepare-for-test`.
2. `handle_prepare_completed()` — callback `server_service`а. На успехе
   стэширует креды в Redis и переводит item в `ready` (ждёт `testing_worker`).
   На провале — retry-логика, при исчерпании — переход к следующему item'у
   очереди стенда или `release-for-service`, если очередь опустела.
3. `claim_next()` — `testing_worker` атомарно забирает один `ready`-item,
   достаёт креды (одноразово), резолвит команду, узнаёт host стенда.
4. `complete_item()` — исход SSH-сессии. Та же retry-логика на провале, та же
   логика продолжения/освобождения очереди.

Бронь стенда держится server_service'ом (`busy_state`) на весь цикл — от
первого `acquire-for-service` до `release-for-service`, когда очередь этого
стенда полностью опустела. Переход между item'ами одной очереди НЕ снимает
бронь — только переключает стадию (`service-status`), это и есть основной
приём этого модуля: `_start_or_continue_cycle()` сначала пробует
`service-status` (бронь уже держится с предыдущего item'а), и только если
server_service отвечает `SERVER_NOT_BUSY` (бронь ещё не бралась — самый
первый item очереди), падает обратно на `acquire-for-service`. Это избавляет
от необходимости отдельно отслеживать «держим ли мы сейчас бронь» — вопрос
решает сам server_service, у которого для этого есть авторитетный источник
истины (`Server.busy_state`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import QueueItemState
from src.core.exceptions import (
    AppException,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import Identity
from src.models import QueueItem
from src.repositories import queue_item as repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as stand_repo
from src.schemas.queue import PrepareForTestCompletedCallback, QueueClaimItem, QueueCompletedRequest
from src.services import audit_service, creds_stash, department_test_settings as dts_svc, server_client
from src.services.test_command_arg import resolve_command
from src.utils.ids import queue_item_id as new_id

logger = logging.getLogger(__name__)

_REQUIRED_LAUNCH_CONTEXT_KEYS = ("RC", "KERNEL", "MODE")


async def enqueue(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    *,
    launch_context: dict[str, str] | None = None,
    debug_mode: bool = False,
    stand_id: str | None = None,
) -> QueueItem:
    """Поставить тест в очередь стенда.

    Вне debug-режима стенд берётся из `test_definitions.pinned_stand_id`
    (тест обязан быть к чему-то привязан). В debug-режиме привязка снимается
    — `stand_id` обязателен и приходит от вызывающего.

    `launch_context` сохраняется как есть, без резолва глобальных переменных
    (это отдельная забота за пределами этой волны) — но обязан нести
    `RC`/`KERNEL`/`MODE`, потому что ими параметризуется `prepare-for-test`.
    """
    test = await test_definition_repo.get_by_id(db, test_id)
    if test is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )

    if debug_mode:
        if not stand_id:
            raise DomainValidationError(
                error_code="STAND_ID_REQUIRED_FOR_DEBUG",
                message="debug_mode requires an explicit stand_id",
            )
        resolved_stand_id = stand_id
    else:
        resolved_stand_id = test.pinned_stand_id
        if not resolved_stand_id:
            raise DomainValidationError(
                error_code="TEST_NOT_PINNED_TO_STAND",
                message="Test has no pinned_stand_id; use debug_mode to pick a stand explicitly",
                details={"test_id": test_id},
            )

    stand = await stand_repo.get_by_id(db, resolved_stand_id)
    if stand is None:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )
    if not stand.is_active:
        raise ConflictError(
            error_code="TEST_STAND_INACTIVE",
            message="Test stand is not active",
        )

    ctx = dict(launch_context or {})
    missing = [key for key in _REQUIRED_LAUNCH_CONTEXT_KEYS if not ctx.get(key)]
    if missing:
        raise DomainValidationError(
            error_code="LAUNCH_CONTEXT_INCOMPLETE",
            message="launch_context must include RC, KERNEL and MODE for prepare-for-test",
            details={"missing": missing},
        )

    was_empty = await repo.count_active_for_stand(db, stand.id) == 0
    position = await repo.next_position_for_stand(db, stand.id)

    data = {
        "id": new_id(),
        "stand_id": stand.id,
        "test_id": test.id,
        "launch_context": ctx,
        "state": QueueItemState.QUEUED,
        "position": position,
        "is_retry": False,
        "retry_of_id": None,
        "debug_mode": debug_mode,
        "created_by": identity.user_id,
    }
    item = await repo.create(db, data)
    await db.commit()
    await db.refresh(item)
    audit_service.emit(
        "queue_item.enqueued",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"test_id": test.id, "stand_id": stand.id, "debug_mode": debug_mode},
    )

    if was_empty:
        await _start_or_continue_cycle(db, stand, item, is_first_ever=True)
        await db.refresh(item)
    return item


async def _start_or_continue_cycle(db: AsyncSession, stand, item: QueueItem, *, is_first_ever: bool) -> None:
    """Взять/продолжить бронь стенда и запросить `prepare-for-test` для `item`.

    `is_first_ever=True` — стенд был свободен, бронь берётся впервые
    (`acquire-for-service`). Иначе бронь уже держится с предыдущего item'а
    этой же очереди — пробуем просто сменить стадию (`service-status`), и
    только если server_service отвечает `SERVER_NOT_BUSY` (бронь почему-то не
    держится), падаем обратно на `acquire-for-service`.
    """
    settings = await dts_svc.get_effective(db, stand.department_id)
    ctx = item.launch_context or {}
    rc = ctx.get("RC")
    kernel = ctx.get("KERNEL")
    mode = ctx.get("MODE")
    note = f"ACS|revert|{rc}|{kernel}"

    try:
        if is_first_ever:
            await server_client.acquire_for_service(
                stand.server_id, busy_state="acs", busy_note=note,
                requested_by_department_id=stand.department_id,
            )
        else:
            try:
                await server_client.set_service_status(stand.server_id, busy_state="acs", busy_note=note)
            except ConflictError as exc:
                if exc.error_code == "SERVER_NOT_BUSY":
                    await server_client.acquire_for_service(
                        stand.server_id, busy_state="acs", busy_note=note,
                        requested_by_department_id=stand.department_id,
                    )
                else:
                    raise
        resp = await server_client.start_prepare_for_test(
            stand.server_id,
            os_version_id=rc,
            kernel=kernel,
            mode=mode,
            test_username=settings["test_username"],
            requested_by_department_id=stand.department_id,
            correlation_id=item.id,
        )
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None,
            error=f"{exc.error_code}: {exc.message}",
            audit_action="queue_item.prepare_start_failed",
            is_first_ever=is_first_ever,
        )
        return

    item.state = QueueItemState.PREPARING
    item.prepare_request_id = resp.get("prepare_request_id")
    await db.commit()
    audit_service.emit(
        "queue_item.prepare_requested",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "prepare_request_id": item.prepare_request_id},
    )


async def _fail_item_and_maybe_retry(
    db: AsyncSession,
    item: QueueItem,
    department_id: str,
    *,
    failed_step: str | None,
    error: str | None,
    audit_action: str,
) -> QueueItem | None:
    """Провалить `item` терминально и, если положено, создать retry в конце очереди."""
    settings = await dts_svc.get_effective(db, department_id)
    item.state = QueueItemState.FAILED
    item.failed_step = failed_step
    item.error = (error or "")[:2048] or None
    item.finished_at = datetime.now(timezone.utc)
    await db.flush()

    audit_service.emit(
        audit_action,
        target_id=item.id, target_type="queue_item",
        status="failure", allowed=True,
        details={"failed_step": failed_step, "error": item.error, "is_retry": item.is_retry},
    )

    if not settings["retry_enabled"] or item.is_retry:
        return None

    position = await repo.next_position_for_stand(db, item.stand_id)
    retry_item = await repo.create(db, {
        "id": new_id(),
        "stand_id": item.stand_id,
        "test_id": item.test_id,
        "launch_context": item.launch_context,
        "state": QueueItemState.QUEUED,
        "position": position,
        "is_retry": True,
        "retry_of_id": item.id,
        "debug_mode": item.debug_mode,
        "created_by": item.created_by,
    })
    audit_service.emit(
        "queue_item.retry_created",
        target_id=retry_item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"retry_of_id": item.id, "stand_id": item.stand_id},
    )
    return retry_item


async def _fail_and_advance(
    db: AsyncSession,
    item: QueueItem,
    stand,
    *,
    failed_step: str | None,
    error: str | None,
    audit_action: str,
    is_first_ever: bool,
) -> None:
    """Провалить `item` + либо продолжить очередь стенда, либо освободить бронь.

    `is_first_ever=True` значит бронь никогда не бралась для этого цикла —
    в этом случае освобождать нечего, даже если queue опустела.
    """
    retry_item = await _fail_item_and_maybe_retry(
        db, item, stand.department_id,
        failed_step=failed_step, error=error, audit_action=audit_action,
    )
    await db.commit()
    if retry_item is not None:
        await _start_or_continue_cycle(db, stand, retry_item, is_first_ever=is_first_ever)
    elif not is_first_ever:
        await _advance_stand_queue(db, stand)


async def _advance_stand_queue(db: AsyncSession, stand) -> None:
    """Взять следующий `queued`-item этого стенда, либо освободить бронь, если очередь пуста."""
    next_item = await repo.get_next_queued_for_stand(db, stand.id)
    if next_item is None:
        try:
            await server_client.release_for_service(stand.server_id)
        except AppException as exc:
            # Best-effort: бронь может повиснуть, если server_service недоступен
            # именно в этот момент. Наблюдаемость — через лог + WARNING-аудит
            # событий выше по цепочке; активной сверки/sweep'а на эту волну не
            # заводили (см. отчёт волны, раздел "вопросы").
            logger.warning(
                "release-for-service failed for stand %s (server %s): %s",
                stand.id, stand.server_id, exc,
            )
        return
    await _start_or_continue_cycle(db, stand, next_item, is_first_ever=False)


async def handle_prepare_completed(
    db: AsyncSession, prepare_request_id: str, body: PrepareForTestCompletedCallback,
) -> QueueItem:
    """Callback server_service'а о завершении `prepare-for-test`."""
    item = await repo.get_by_prepare_request_id(db, prepare_request_id)
    if item is None:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND_FOR_PREPARE_REQUEST",
            message="No queue item is waiting on this prepare_request_id",
            details={"prepare_request_id": prepare_request_id},
        )
    if item.state != QueueItemState.PREPARING:
        # Повторный callback по уже обработанному запросу — идемпотентный
        # no-op (server_service ретраит доставку несколько раз).
        return item

    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        # Не должно случаться — FK RESTRICT не даёт снести стенд, пока на
        # него ссылается хоть один queue_item. Оставлено как защита от
        # рассинхрона данных, не рабочий путь.
        raise ServiceUnavailableError(
            error_code="TEST_STAND_MISSING",
            message="Test stand referenced by this queue item no longer exists",
        )

    if body.succeeded:
        stash_key = creds_stash.new_stash_key(item.id)
        await creds_stash.store_creds(stash_key, {
            "test_username": body.test_username,
            "test_password": body.test_password,
            "test_ssh_private_key": body.test_ssh_private_key,
        })
        item.state = QueueItemState.READY
        item.creds_stash_key = stash_key
        item.error = None
        await db.commit()
        if body.warning:
            logger.warning(
                "prepare-for-test succeeded with a warning for queue_item %s: %s",
                item.id, body.warning,
            )
        audit_service.emit(
            "queue_item.ready",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"prepare_request_id": prepare_request_id, "has_warning": bool(body.warning)},
        )
        return item

    await _fail_and_advance(
        db, item, stand,
        failed_step=body.failed_step, error=body.error,
        audit_action="queue_item.prepare_failed",
        is_first_ever=False,
    )
    return item


async def claim_next(db: AsyncSession) -> QueueClaimItem | None:
    """Атомарно забрать один `ready`-item (по любому стенду) для `testing_worker`."""
    item = await repo.claim_next_ready(db)
    if item is None:
        return None

    stand = await stand_repo.get_by_id(db, item.stand_id)
    test = await test_definition_repo.get_by_id(db, item.test_id)
    if stand is None or test is None:
        # См. аналогичный комментарий в `handle_prepare_completed` — FK
        # RESTRICT делает это недостижимым в норме.
        raise ServiceUnavailableError(
            error_code="QUEUE_ITEM_DATA_MISSING",
            message="Stand or test definition referenced by this queue item no longer exists",
        )

    creds = None
    if item.creds_stash_key:
        creds = await creds_stash.pop_creds(item.creds_stash_key)
    if creds is None:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error="creds stash missing or expired before claim",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    item.state = QueueItemState.RUNNING
    item.started_at = datetime.now(timezone.utc)
    await db.commit()

    ctx = item.launch_context or {}
    busy_note = f"{test.code}|{ctx.get('RC', '')}|{ctx.get('KERNEL', '')}"
    try:
        await server_client.set_service_status(stand.server_id, busy_state="testing", busy_note=busy_note)
    except AppException as exc:
        # Best-effort: неудачная смена стадии не должна срывать уже
        # выданное задание — SSH-сессия не зависит от busy_note, это
        # информационная метка для UI/оператора.
        logger.warning("service-status(testing) failed for queue_item %s: %s", item.id, exc)

    try:
        command = await resolve_command(db, item.test_id, ctx)
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error=f"resolve_command failed: {exc.message}",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    try:
        connection = await server_client.get_connection_info(stand.server_id)
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error=f"connection-info failed: {exc.message}",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    audit_service.emit(
        "queue_item.claimed",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "test_id": test.id},
    )
    return QueueClaimItem(
        queue_item_id=item.id,
        host=connection["host"],
        test_username=creds.get("test_username"),
        test_password=creds.get("test_password"),
        test_ssh_private_key=creds.get("test_ssh_private_key"),
        command=command,
        debug_mode=item.debug_mode,
        is_retry=item.is_retry,
    )


async def complete_item(db: AsyncSession, queue_item_id: str, body: QueueCompletedRequest) -> QueueItem:
    """Исход SSH-исполнения от `testing_worker`."""
    item = await repo.get_by_id_for_update(db, queue_item_id)
    if item is None:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND",
            message="Queue item not found",
        )
    if item.state != QueueItemState.RUNNING:
        # Идемпотентный no-op — дубль уже обработанного completion'а.
        return item

    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        raise ServiceUnavailableError(
            error_code="TEST_STAND_MISSING",
            message="Test stand referenced by this queue item no longer exists",
        )

    if body.succeeded:
        item.state = QueueItemState.SUCCEEDED
        item.finished_at = datetime.now(timezone.utc)
        item.error = None
        await db.commit()
        audit_service.emit(
            "queue_item.completed",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"exit_code": body.exit_code, "stand_id": stand.id},
        )
        await _advance_stand_queue(db, stand)
        return item

    error = body.error or (
        f"SSH run failed with exit_code={body.exit_code}"
        if body.exit_code is not None else "SSH run failed"
    )
    await _fail_and_advance(
        db, item, stand,
        failed_step=None, error=error,
        audit_action="queue_item.completed",
        is_first_ever=False,
    )
    return item
