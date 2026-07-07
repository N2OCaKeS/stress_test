"""Vm-репозиторий — сырой CRUD против таблицы `vms` + агрегаты ёмкости hub'а."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Vm


async def get_by_id(db: AsyncSession, vm_id: str) -> Vm | None:
    """SELECT ВМ по PK."""
    return (
        await db.execute(select(Vm).where(Vm.id == vm_id))
    ).scalar_one_or_none()


async def get_by_number(db: AsyncSession, number: int) -> Vm | None:
    """SELECT ВМ по номеру стенда (глобально уникален)."""
    return (
        await db.execute(select(Vm).where(Vm.number == number))
    ).scalar_one_or_none()


async def list_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Vm]:
    """SELECT ВМ, опционально ограниченный набором отделов (created_at DESC).

    `department_ids=None` → все (зарезервировано); `[]` → пусто.
    """
    stmt = select(Vm).order_by(Vm.created_at.desc(), Vm.id.desc()).limit(limit).offset(offset)
    if department_ids is not None:
        if not department_ids:
            return []
        stmt = stmt.where(Vm.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def count_in_departments(
    db: AsyncSession, department_ids: list[str] | None,
) -> int:
    """COUNT под тем же фильтром, что и list — для total в pagination."""
    stmt = select(func.count(Vm.id))
    if department_ids is not None:
        if not department_ids:
            return 0
        stmt = stmt.where(Vm.department_id.in_(department_ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_by_ids(
    db: AsyncSession, ids: list[str], *, limit: int, offset: int,
) -> list[Vm]:
    """Grant-only листинг: ВМ строго из набора id (created_at DESC)."""
    if not ids:
        return []
    stmt = (
        select(Vm)
        .where(Vm.id.in_(ids))
        .order_by(Vm.created_at.desc(), Vm.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_by_ids(db: AsyncSession, ids: list[str]) -> int:
    """COUNT для grant-only листинга."""
    if not ids:
        return 0
    stmt = select(func.count(Vm.id)).where(Vm.id.in_(ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_for_hub(db: AsyncSession, hub_server_id: str) -> list[Vm]:
    """Все ВМ конкретного hub'а — для проверки ёмкости и вложенного list'а."""
    stmt = select(Vm).where(Vm.hub_server_id == hub_server_id).order_by(Vm.created_at)
    return list((await db.execute(stmt)).scalars())


async def sum_resources_for_hub(db: AsyncSession, hub_server_id: str) -> dict[str, int]:
    """Σ(cpu / ram_mb / disk_gb) уже созданных ВМ на hub'е (NULL → 0)."""
    stmt = select(
        func.coalesce(func.sum(Vm.cpu), 0),
        func.coalesce(func.sum(Vm.ram_mb), 0),
        func.coalesce(func.sum(Vm.disk_gb), 0),
    ).where(Vm.hub_server_id == hub_server_id)
    row = (await db.execute(stmt)).one()
    return {"cpu": int(row[0]), "ram_mb": int(row[1]), "disk_gb": int(row[2])}


async def create(db: AsyncSession, data: dict) -> Vm:
    """INSERT новой ВМ (без commit — owner транзакции caller)."""
    obj = Vm(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, vm: Vm) -> None:
    """DELETE строки ВМ (без commit)."""
    await db.delete(vm)
    await db.flush()
