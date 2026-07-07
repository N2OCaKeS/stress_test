"""VmSnapshot-репозиторий — сырой CRUD против таблицы `vm_snapshots`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmSnapshot


async def get_by_id(db: AsyncSession, snapshot_id: str) -> VmSnapshot | None:
    """SELECT снимка по PK."""
    return (
        await db.execute(select(VmSnapshot).where(VmSnapshot.id == snapshot_id))
    ).scalar_one_or_none()


async def get_by_name(
    db: AsyncSession, vm_id: str, name: str
) -> VmSnapshot | None:
    """SELECT снимка ВМ по имени (уникально в пределах ВМ)."""
    stmt = select(VmSnapshot).where(
        VmSnapshot.vm_id == vm_id, VmSnapshot.name == name
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_vm(
    db: AsyncSession, vm_id: str, *, include_system: bool = False
) -> list[VmSnapshot]:
    """Снимки ВМ (created_at ASC). `include_system=False` прячет `<ver>_build`."""
    stmt = select(VmSnapshot).where(VmSnapshot.vm_id == vm_id)
    if not include_system:
        stmt = stmt.where(VmSnapshot.is_system.is_(False))
    stmt = stmt.order_by(VmSnapshot.created_at, VmSnapshot.id)
    return list((await db.execute(stmt)).scalars())


async def list_all_for_vm(db: AsyncSession, vm_id: str) -> list[VmSnapshot]:
    """Все снимки ВМ, включая системные — для sync-callback'а и ротации."""
    return await list_for_vm(db, vm_id, include_system=True)


async def get_current(db: AsyncSession, vm_id: str) -> VmSnapshot | None:
    """Текущий снимок ВМ (is_current=True), если есть."""
    stmt = select(VmSnapshot).where(
        VmSnapshot.vm_id == vm_id, VmSnapshot.is_current.is_(True)
    )
    return (await db.execute(stmt)).scalars().first()


async def create(db: AsyncSession, data: dict) -> VmSnapshot:
    """INSERT снимка (без commit — owner транзакции caller)."""
    obj = VmSnapshot(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, snapshot: VmSnapshot) -> None:
    """DELETE строки снимка (без commit)."""
    await db.delete(snapshot)
    await db.flush()
