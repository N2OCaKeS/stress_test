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
    """
    Получить список всех диапазонов IP.

    Доступ: только администраторы (задаётся на уровне роутера).
    Возвращает: массив объектов IPRangeRead.
    Коды ошибок: отсутствуют.
    """
    return await get_ip_ranges(db)


@router.get("/{range_id}", response_model=IPRangeRead)
async def get_single_ip_range(
    range_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Получить один диапазон IP по идентификатору.

    Доступ: только администраторы.
    Параметры:
      - range_id: целочисленный идентификатор диапазона.
    Возвращает: объект IPRangeRead.
    Коды ошибок:
      - 404 — диапазон не найден.
    """
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
    """
    Создать новый диапазон IP.

    Доступ: только администраторы.
    Тело запроса: IPRangeCreate.
    Возвращает: созданный объект IPRangeRead.
    Коды ошибок:
      - 409 — диапазон с таким именем уже существует.
      - 500 — ошибка при создании диапазона.
    """
    exists = await db.execute(select(IPRange.id).where(IPRange.name == data.name))
    if exists.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"IP range with name {data.name!r} already exists",
        )
    try:
        return await create_ip_range(db, data)
    except IntegrityError as e:
        await db.rollback()
        pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
        if pgcode == "23505" or "unique" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"IP range with name {data.name!r} already exists",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create IP range",
        )


@router.patch("/{range_id}", response_model=IPRangeRead)
async def patch_ip_range(
    range_id: int, data: IPRangeUpdate, db: AsyncSession = Depends(get_async_db)
):
    """
    Частично обновить диапазон IP.

    Доступ: только администраторы.
    Параметры:
      - range_id: идентификатор обновляемого диапазона.
    Тело запроса: IPRangeUpdate (минимум одно поле).
    Возвращает: обновлённый объект IPRangeRead.
    Коды ошибок:
      - 400 — не передано ни одного поля для обновления.
      - 404 — диапазон не найден.
    """
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
    """
    Удалить диапазон IP.

    Доступ: только администраторы.
    Параметры:
      - range_id: идентификатор удаляемого диапазона.
    Возвращает: пустой ответ с кодом 204.
    Коды ошибок:
      - 404 — диапазон не найден.
    """
    ok = await delete_ip_range(db, range_id)
    if not ok:
        raise HTTPException(status_code=404, detail="IP Range not found")
