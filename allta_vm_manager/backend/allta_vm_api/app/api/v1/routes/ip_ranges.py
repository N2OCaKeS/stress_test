from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.v1.schemas.ip_range import (
    IPRangeRead,
    IPRangeCreate,
    IPRangeUpdate,
)
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
    return await create_ip_range(db, data)


@router.put(
    "/{range_id}",
    response_model=IPRangeRead,
)
async def update_existing_ip_range(
    range_id: int,
    data: IPRangeUpdate,
    db: AsyncSession = Depends(get_async_db),
):
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
