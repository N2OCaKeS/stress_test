"""Публичная очередь, отдельная от протокола testing_worker."""

from typing import Literal
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
from src.services import public_queue as svc

router = APIRouter(prefix="/queue-items")


@router.post("", response_model=PublicQueueItem, status_code=201)
async def launch(
    body: QueueLaunchRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    return svc.response(await svc.launch(db, identity, body))


@router.post("/{item_id}/retry", response_model=PublicQueueItem, status_code=201)
async def retry(
    item_id: str,
    body: QueueRetryRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
):
    return svc.response(await svc.retry(db, identity, item_id, body))


@router.get("", response_model=PaginatedResponse[PublicQueueItem])
async def list_items(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    kind: Literal["standalone", "campaign", "all"] = "standalone",
    test_run_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    items, total = await repo.list_for_department(
        db,
        identity.department_id or "",
        kind=kind,
        test_run_id=test_run_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[svc.response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
