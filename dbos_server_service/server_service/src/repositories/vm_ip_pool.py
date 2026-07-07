"""VmIpPool-репозиторий — CRUD пулов IP-адресов ВМ (IPAM)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmIpPool


async def get_by_id(db: AsyncSession, pool_id: str) -> VmIpPool | None:
    """SELECT пула по PK."""
    return (
        await db.execute(select(VmIpPool).where(VmIpPool.id == pool_id))
    ).scalar_one_or_none()


async def list_in_department(
    db: AsyncSession, department_id: str, *, limit: int = 100, offset: int = 0,
) -> list[VmIpPool]:
    """Пулы отдела (created_at DESC)."""
    stmt = (
        select(VmIpPool)
        .where(VmIpPool.department_id == department_id)
        .order_by(VmIpPool.created_at.desc(), VmIpPool.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_in_department(db: AsyncSession, department_id: str) -> int:
    """COUNT пулов отдела — для total в pagination."""
    stmt = select(func.count(VmIpPool.id)).where(
        VmIpPool.department_id == department_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> VmIpPool:
    """INSERT пула (без commit — owner транзакции caller)."""
    obj = VmIpPool(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, pool: VmIpPool) -> None:
    """DELETE строки пула (без commit)."""
    await db.delete(pool)
    await db.flush()
