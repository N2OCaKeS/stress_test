from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.ip_range import IPRange
from app.api.v1.schemas.ip_range import IPRangeCreate, IPRangeUpdate


async def get_ip_range(db: AsyncSession, range_id: int) -> Optional[IPRange]:
    result = await db.execute(
        select(IPRange).where(IPRange.id == range_id)
    )
    return result.scalars().first()


async def get_ip_ranges(db: AsyncSession) -> List[IPRange]:
    result = await db.execute(select(IPRange))
    return result.scalars().all()


async def create_ip_range(db: AsyncSession, data: IPRangeCreate) -> IPRange:
    ip_range = IPRange(**data.model_dump())
    db.add(ip_range)
    try:
        await db.commit()
        await db.refresh(ip_range)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to create IP range: {str(e)}")
    return ip_range


async def update_ip_range(db: AsyncSession, range_id: int, data: IPRangeUpdate) -> Optional[IPRange]:
    obj = await get_ip_range(db, range_id)
    if not obj:
        return None
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_ip_range(db: AsyncSession, range_id: int) -> bool:
    ip_range = await get_ip_range(db, range_id)
    if not ip_range:
        return False

    await db.delete(ip_range)
    await db.commit()
    return True
