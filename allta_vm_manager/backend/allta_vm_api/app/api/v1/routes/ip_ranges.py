from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.db.session import get_async_db
from app.api.v1.schemas.ip_range import (
    IPRangeRead,
    IPRangeCreate,
    IPRangeUpdate,
)
from app.api.v1.models.ip_range import IPRange
from app.api.v1.dependencies import get_current_admin_user
from app.api.v1.crud.ip_ranges import (
    get_ip_range,
    get_ip_ranges,
    create_ip_range,
    update_ip_range,
    delete_ip_range,
)

router = APIRouter(
    prefix="/ip-ranges",
    tags=["IP Ranges"],
    dependencies=[Depends(get_current_admin_user)],
)


@router.get("/", response_model=List[IPRangeRead])
async def list_ip_ranges(
    db: AsyncSession = Depends(get_async_db),
):
    return await get_ip_ranges(db)


@router.get("/{range_id}", response_model=IPRangeRead)
async def get_single_ip_range(
    range_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    record = await get_ip_range(db, range_id)
    if not record:
        raise HTTPException(status_code=404, detail="IP Range not found")
    return record


@router.post(
    "/",
    response_model=IPRangeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_ip_range(
    data: IPRangeCreate,
    db: AsyncSession = Depends(get_async_db),
):
    # Быстрый предчек (ускоряет ответ и даёт красивую ошибку; от гонок не защищает)
    exists = await db.execute(select(IPRange.id).where(IPRange.name == data.name))
    if exists.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"IP range with name {data.name!r} already exists",
        )

    # Основное создание + защита от гонок (уникальный индекс/constraint в БД)
    try:
        return await create_ip_range(db, data)
    except IntegrityError as e:
        # В случае падения коммита внутри CRUD — сессия в ошибочном состоянии, откатим
        await db.rollback()
        # Для Postgres это обычно код 23505 (unique_violation)
        pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
        if pgcode == "23505" or "unique" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"IP range with name {data.name!r} already exists",
            )
        # Если ошибка другая — пробросим дальше как 500
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create IP range",
        )


@router.patch("/{range_id}", response_model=IPRangeRead)
async def patch_ip_range(
    range_id: int, data: IPRangeUpdate, db: AsyncSession = Depends(get_async_db)
):
    if not data.model_dump(exclude_unset=True):
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await update_ip_range(db, range_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="IP Range not found")
    return result


@router.delete(
    "/{range_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_ip_range_endpoint(
    range_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    ok = await delete_ip_range(db, range_id)
    if not ok:
        raise HTTPException(status_code=404, detail="IP Range not found")
