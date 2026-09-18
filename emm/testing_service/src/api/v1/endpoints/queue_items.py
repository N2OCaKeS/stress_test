"""Публичная очередь, отдельная от протокола testing_worker."""

from typing import Literal
from pydantic import AwareDatetime
from src.core.exceptions import DomainValidationError
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.public_queue import (
    PublicQueueItem,
    QueueLaunchRequest,
    QueueRetryRequest,
)
from src.repositories import queue_item as repo
from src.services import public_queue as svc, log_availability

router = APIRouter(prefix="/queue-items")


@router.post("", response_model=PublicQueueItem, status_code=201)
async def launch(
    body: QueueLaunchRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    item = await svc.launch(db, identity, body)
    logs = await log_availability.for_items(db, [item])
    return svc.response(item).model_copy(update={"log_status": logs[item.id]})


@router.post("/{item_id}/retry", response_model=PublicQueueItem, status_code=201)
async def retry(
    item_id: str,
    body: QueueRetryRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    item = await svc.retry(db, identity, item_id, body)
    logs = await log_availability.for_items(db, [item])
    return svc.response(item).model_copy(update={"log_status": logs[item.id]})


@router.post(
    "/{item_id}/skip",
    response_model=PublicQueueItem,
    summary="Пропустить элемент очереди",
    description=(
        "Снимает тест с исполнения без исхода: элемент уходит в `skipped`, "
        "стенд сразу продолжает со следующего. Если элемент прямо сейчас "
        "исполняется на стенде (`running`), моментально оборвать его нельзя — "
        "ответ вернёт тот же `running`-элемент с `interrupt_action=\"skip\"`, "
        "а фактический обрыв сделает `testing_worker` при ближайшем опросе."
    ),
    responses={
        403: {"description": "Нет прав на стенд этого элемента."},
        404: {"description": "Элемент очереди не найден."},
        409: {"description": "QUEUE_ITEM_NOT_ACTIVE — элемент уже завершён."},
    },
)
async def skip(
    item_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    item = await svc.skip(db, identity, item_id)
    logs = await log_availability.for_items(db, [item])
    return svc.response(item).model_copy(update={"log_status": logs[item.id]})


@router.post(
    "/{item_id}/pause",
    response_model=PublicQueueItem,
    summary="Остановить элемент очереди",
    description=(
        "То же прерывание, что и `skip`, но исход не фиксируется: элемент "
        "уходит в `paused` и остаётся в очереди стенда, стенд встаёт и ждёт "
        "`POST /test-stands/{stand_id}/resume-queue`. Для `running`-элемента "
        "ответ несёт `interrupt_action=\"pause\"` — см. `skip`."
    ),
    responses={
        403: {"description": "Нет прав на стенд этого элемента."},
        404: {"description": "Элемент очереди не найден."},
        409: {"description": "QUEUE_ITEM_NOT_ACTIVE — элемент уже завершён."},
    },
)
async def pause(
    item_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    item = await svc.pause(db, identity, item_id)
    logs = await log_availability.for_items(db, [item])
    return svc.response(item).model_copy(update={"log_status": logs[item.id]})


@router.delete(
    "/{item_id}",
    response_model=OkResponse,
    summary="Удалить элемент очереди",
    description=(
        "Убирает элемент из очереди насовсем — в отличие от `skip`, "
        "терминальной записи не остаётся. Работает для ещё не стартовавших "
        "и уже терминальных элементов; для того, что прямо сейчас занимает "
        "стенд (`preparing`/`ready`/`running`), сначала нужен `skip`/`pause`."
    ),
    responses={
        403: {"description": "Нет прав на стенд этого элемента."},
        404: {"description": "Элемент очереди не найден."},
        409: {"description": "QUEUE_ITEM_IN_PROGRESS — элемент сейчас занимает стенд."},
    },
)
async def delete_queue_item(
    item_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    await svc.delete(db, identity, item_id)
    return OkResponse()


@router.get("", response_model=PaginatedResponse[PublicQueueItem])
async def list_items(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    kind: Literal["standalone", "campaign", "all"] = "standalone",
    test_run_id: str | None = None,
    os_version_id: str | None = None,
    os_version_name: str | None = Query(None, max_length=64),
    kernel: str | None = None,
    stand_id: str | None = None,
    test_id: str | None = None,
    attempt_id: str | None = None,
    retry_of_id: str | None = None,
    created_from: AwareDatetime | None = None,
    created_until: AwareDatetime | None = None,
    states: list[Literal[
        "queued", "preparing", "ready", "running",
        "succeeded", "failed", "skipped", "paused",
    ]] | None = Query(None),
    debug_mode: bool | None = None,
    q: str | None = Query(None, max_length=200),
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if created_from and created_until and created_from >= created_until:
        raise DomainValidationError(
            error_code="INVALID_TIME_RANGE", message="Начало периода должно быть раньше конца"
        )
    items, total = await repo.list_for_department(
        db,
        identity.department_id or "",
        kind=kind,
        test_run_id=test_run_id,
        limit=limit,
        offset=offset,
        os_version_id=os_version_id, os_version_name=os_version_name, kernel=kernel, stand_id=stand_id, test_id=test_id, attempt_id=attempt_id,
        retry_of_id=retry_of_id, created_from=created_from, created_until=created_until,
        states=states, debug_mode=debug_mode, q=q, order=order,
    )
    logs = await log_availability.for_items(db, [row[0] for row in items])
    return PaginatedResponse(
        items=[svc.response(item).model_copy(update={
            "test_code": code, "test_name": name, "is_current": current, "log_status": logs[item.id],
        }) for item, code, name, current in items],
        total=total,
        limit=limit,
        offset=offset,
    )
