"""Публичная очередь, отдельная от протокола testing_worker."""

from typing import Literal
from pydantic import AwareDatetime
from src.core.exceptions import DomainValidationError
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import PaginatedResponse
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
    states: list[Literal["queued", "preparing", "ready", "running", "succeeded", "failed"]] | None = Query(None),
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
