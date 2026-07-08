"""Репозиторий инвентаря пакетов ВМ — get + upsert (одна строка на ВМ)."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmPackageInventory


async def get_for_vm(db: AsyncSession, vm_id: str) -> VmPackageInventory | None:
    """SELECT инвентаря по vm_id (PK). None — probe ещё не делали."""
    stmt = select(VmPackageInventory).where(VmPackageInventory.vm_id == vm_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert(
    db: AsyncSession,
    vm_id: str,
    *,
    packages: list[dict],
    source: str | None,
    task_id: str | None,
) -> VmPackageInventory:
    """Создать/перезаписать инвентарь пакетов ВМ целиком. commit — на caller'е."""
    row = await get_for_vm(db, vm_id)
    now = datetime.now(timezone.utc)
    if row is None:
        row = VmPackageInventory(
            vm_id=vm_id,
            packages=packages,
            package_count=len(packages),
            source=source,
            task_id=task_id,
            synced_at=now,
        )
        db.add(row)
    else:
        row.packages = packages
        row.package_count = len(packages)
        row.source = source
        row.task_id = task_id
        row.synced_at = now
    await db.flush()
    return row
