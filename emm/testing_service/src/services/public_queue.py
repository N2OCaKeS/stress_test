"""Публичные запуски: права отдела, идемпотентность и сохранение контекста retry."""

import hashlib
import json
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import ACTIVE_QUEUE_STATES, Action, EntityType, QueueInterruptAction
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import TestRun
from src.repositories import (
    queue_item as repo,
    test_stand as stands,
    test_definition as tests,
)
from src.schemas.public_queue import (
    PublicQueueItem,
    QueueLaunchRequest,
    QueueRetryRequest,
)
from src.services import permissions, queue, launch_stp


def response(item):
    ctx = item.launch_context or {}
    return PublicQueueItem(
        **{
            key: getattr(item, key)
            for key in (
                "id",
                "test_id",
                "stand_id",
                "test_run_id",
                "retry_of_id",
                "debug_mode",
                "prepare_only",
                "state",
                "created_at",
                "started_at",
                "finished_at",
                "error",
                "interrupt_action",
            )
        },
        rc=ctx.get("RC"),
        kernel=ctx.get("KERNEL"),
        mode=ctx.get("MODE"),
    )


async def authorize(db, identity, stand_id):
    stand = await stands.get_by_id(db, stand_id)
    if not stand:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND", message="Стенд не найден"
        )
    await permissions.require_department_action(
        db, identity, stand.department_id, EntityType.TEST_RUN, Action.CREATE
    )
    return stand


async def request(db, identity, request_id, payload):
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    await repo.lock_request(db, identity.user_id, request_id)
    existing = await repo.find_request(db, identity.user_id, request_id)
    if existing and existing.request_fingerprint != fingerprint:
        raise ConflictError(
            error_code="REQUEST_ID_CONFLICT",
            message="Этот идентификатор запроса уже использован с другими параметрами",
        )
    return existing, fingerprint


async def launch(db: AsyncSession, identity: Identity, body: QueueLaunchRequest):
    stand = await authorize(db, identity, body.stand_id)
    existing, fingerprint = await request(
        db, identity, body.request_id, body.model_dump(exclude={"request_id"})
    )
    if existing:
        return existing
    test = await tests.get_by_id(db, body.test_id, for_update=True)
    if not test:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND", message="Тест не найден"
        )
    if test.department_id and test.department_id != stand.department_id:
        raise AuthorizationError(
            error_code="PERMISSION_DENIED", message="Тест принадлежит другому отделу"
        )
    if not body.debug_mode and test.pinned_stand_id != body.stand_id:
        raise DomainValidationError(
            error_code="TEST_STAND_MISMATCH",
            message="Для обычного запуска выберите закреплённый стенд",
        )
    ctx = {"RC": body.os_version_id, "KERNEL": body.kernel, "MODE": test.mode}
    stp = (
        None
        if body.debug_mode
        else await launch_stp.require_membership(db, test.code, stand.id, ctx)
    )
    return await queue.enqueue(
        db,
        identity,
        test.id,
        launch_context=ctx,
        debug_mode=body.debug_mode,
        prepare_only=body.prepare_only,
        stand_id=stand.id,
        client_request_id=body.request_id,
        request_fingerprint=fingerprint,
        stp_test_run_id=stp.id if stp else None,
        force=body.force,
    )


async def _authorize_item(db: AsyncSession, identity: Identity, item_id: str):
    """Общая для skip/pause часть: найти элемент и проверить права на его стенд.

    Тот же допуск, что у `retry()` — владение стендом через матрицу отдела
    плюс проверка, что чужая кампания не управляется из другого отдела.
    """
    item = await repo.get_by_id(db, item_id)
    if not item:
        raise NotFoundError(error_code="QUEUE_ITEM_NOT_FOUND", message="Попытка не найдена")
    stand = await authorize(db, identity, item.stand_id)
    if item.test_run_id:
        run = await db.get(TestRun, item.test_run_id)
        if not run or run.department_id != identity.department_id:
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="Прогон принадлежит другому отделу",
            )
    locked = await repo.get_by_id_for_update(db, item_id)
    return locked, stand


async def skip(db: AsyncSession, identity: Identity, item_id: str):
    """Снять элемент очереди с исполнения без исхода и поехать дальше."""
    item, stand = await _authorize_item(db, identity, item_id)
    return await queue.request_interrupt(db, item, stand, QueueInterruptAction.SKIP)


async def pause(db: AsyncSession, identity: Identity, item_id: str):
    """Снять элемент очереди с исполнения и остановить стенд до `resume-queue`."""
    item, stand = await _authorize_item(db, identity, item_id)
    return await queue.request_interrupt(db, item, stand, QueueInterruptAction.PAUSE)


async def resume_stand_queue(db: AsyncSession, identity: Identity, stand_id: str):
    """Вернуть остановленный элемент стенда в конец очереди и продолжить её."""
    stand = await authorize(db, identity, stand_id)
    return await queue.resume_stand_queue(db, stand)


async def clear_queue(db: AsyncSession, identity: Identity, stand_id: str) -> int:
    """Очистить очередь стенда от ещё не стартовавших item'ов. Возвращает их число."""
    stand = await authorize(db, identity, stand_id)
    return await queue.clear_queue(db, stand)


async def retry_failed(db: AsyncSession, identity: Identity, stand_id: str) -> tuple[list, int]:
    """Повторить разом все упавшие item'ы стенда, ещё не перезапущенные."""
    stand = await authorize(db, identity, stand_id)
    return await queue.retry_failed(db, identity, stand)


async def delete(db: AsyncSession, identity: Identity, item_id: str) -> None:
    """Убрать элемент очереди насовсем — доступ такой же, как у skip/pause."""
    item, stand = await _authorize_item(db, identity, item_id)
    await queue.delete_item(db, item, stand)


async def retry(
    db: AsyncSession, identity: Identity, item_id: str, body: QueueRetryRequest
):
    source = await repo.get_by_id(db, item_id)
    if not source:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND", message="Попытка не найдена"
        )
    stand = await authorize(db, identity, source.stand_id)
    if source.test_run_id:
        run = await db.get(TestRun, source.test_run_id)
        if not run or run.department_id != identity.department_id:
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="Прогон принадлежит другому отделу",
            )
    existing, fingerprint = await request(
        db, identity, body.request_id, {"retry_of_id": item_id}
    )
    if existing:
        return existing
    source = await repo.get_by_id_for_update(db, item_id)
    if source.state in ACTIVE_QUEUE_STATES or await repo.has_successor(db, item_id):
        raise ConflictError(
            error_code="RETRY_NOT_ALLOWED",
            message="Повторить можно только последнюю завершённую попытку",
        )
    test = await tests.get_by_id(db, source.test_id, for_update=True)
    if not test:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND", message="Тест не найден"
        )
    if test.department_id and test.department_id != stand.department_id:
        raise AuthorizationError(
            error_code="PERMISSION_DENIED", message="Тест принадлежит другому отделу"
        )
    stp = (
        None
        if source.debug_mode
        else await launch_stp.require_membership(
            db, test.code, stand.id, source.launch_context, source.stp_test_run_id
        )
    )
    return await queue.enqueue(
        db,
        identity,
        source.test_id,
        launch_context=dict(source.launch_context),
        debug_mode=source.debug_mode,
        prepare_only=source.prepare_only,
        stand_id=source.stand_id,
        test_run_id=source.test_run_id,
        test_run_entry_id=source.test_run_entry_id,
        retry_source=source,
        client_request_id=body.request_id,
        request_fingerprint=fingerprint,
        stp_test_run_id=stp.id if stp else None,
    )
