"""VmPreset-репозиторий — CRUD шаблонов стандартных ВМ отдела (vm_preset)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmPreset


async def get_by_id(db: AsyncSession, preset_id: str) -> VmPreset | None:
    """SELECT пресета по PK."""
    return (
        await db.execute(select(VmPreset).where(VmPreset.id == preset_id))
    ).scalar_one_or_none()


async def list_in_department(
    db: AsyncSession, department_id: str, *, limit: int = 100, offset: int = 0,
) -> list[VmPreset]:
    """Пресеты отдела (created_at DESC)."""
    stmt = (
        select(VmPreset)
        .where(VmPreset.department_id == department_id)
        .order_by(VmPreset.created_at.desc(), VmPreset.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_in_department(db: AsyncSession, department_id: str) -> int:
    """COUNT пресетов отдела — для total в pagination."""
    stmt = select(func.count(VmPreset.id)).where(
        VmPreset.department_id == department_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_all_in_department(db: AsyncSession, department_id: str) -> list[VmPreset]:
    """Все пресеты отдела без пагинации — для create-default-vms."""
    stmt = (
        select(VmPreset)
        .where(VmPreset.department_id == department_id)
        .order_by(VmPreset.created_at, VmPreset.id)
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> VmPreset:
    """INSERT пресета (без commit — owner транзакции caller)."""
    obj = VmPreset(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, preset: VmPreset) -> None:
    """DELETE строки пресета (без commit)."""
    await db.delete(preset)
    await db.flush()
