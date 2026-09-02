"""VmDisk-репозиторий — CRUD против таблицы `vm_disks`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmDisk


async def get_by_id(db: AsyncSession, disk_id: str) -> VmDisk | None:
    """SELECT диска по PK."""
    return (
        await db.execute(select(VmDisk).where(VmDisk.id == disk_id))
    ).scalar_one_or_none()


async def list_for_vm(db: AsyncSession, vm_id: str) -> list[VmDisk]:
    """Все диски ВМ (created_at ASC — системный обычно первый)."""
    stmt = select(VmDisk).where(VmDisk.vm_id == vm_id).order_by(VmDisk.created_at)
    return list((await db.execute(stmt)).scalars())


async def sum_size_for_vm(db: AsyncSession, vm_id: str) -> int:
    """Σ(size_gb) всех дисков ВМ (для проверки ёмкости пула при добавлении)."""
    stmt = select(func.coalesce(func.sum(VmDisk.size_gb), 0)).where(
        VmDisk.vm_id == vm_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> VmDisk:
    """INSERT нового диска (без commit — owner транзакции caller)."""
    obj = VmDisk(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, disk: VmDisk) -> None:
    """DELETE строки диска (без commit)."""
    await db.delete(disk)
    await db.flush()
