"""TestStand-репозиторий — сырой CRUD против таблицы `test_stands`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestStand


async def get_by_id(db: AsyncSession, stand_id: str, *, for_update: bool = False) -> TestStand | None:
    """SELECT по PK."""
    stmt = select(TestStand).where(TestStand.id == stand_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_server_id(db: AsyncSession, server_id: str) -> TestStand | None:
    """SELECT по UNIQUE server_id."""
    stmt = select(TestStand).where(TestStand.server_id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_vm_id(db: AsyncSession, vm_id: str) -> TestStand | None:
    """SELECT по UNIQUE vm_id (ВМ-стенд)."""
    stmt = select(TestStand).where(TestStand.vm_id == vm_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_legacy_token(db: AsyncSession, legacy_token: str) -> TestStand | None:
    """SELECT по UNIQUE legacy_token (`stand3`..`stand14` из allta_app)."""
    stmt = select(TestStand).where(TestStand.legacy_token == legacy_token)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_ids(db: AsyncSession, stand_ids: list[str]) -> list[TestStand]:
    """SELECT пачкой по PK — чтобы отчёты не делали N+1 ради одного алиаса."""
    if not stand_ids:
        return []
    stmt = select(TestStand).where(TestStand.id.in_(stand_ids))
    return list((await db.execute(stmt)).scalars())


def _apply_filters(
    stmt,
    *,
    department_id: str | None,
    is_active: bool | None,
    queue_enabled: bool | None,
    server_id: str | None = None,
    vm_id: str | None = None,
):
    if department_id is not None:
        stmt = stmt.where(TestStand.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(TestStand.is_active == is_active)
    if queue_enabled is not None:
        stmt = stmt.where(TestStand.queue_enabled == queue_enabled)
    if server_id is not None:
        stmt = stmt.where(TestStand.server_id == server_id)
    if vm_id is not None:
        stmt = stmt.where(TestStand.vm_id == vm_id)
    return stmt


async def list_all(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    *,
    department_id: str | None = None,
    is_active: bool | None = None,
    queue_enabled: bool | None = None,
    server_id: str | None = None,
    vm_id: str | None = None,
) -> list[TestStand]:
    """Страница стендов с опциональными фильтрами, order by created_at."""
    stmt = _apply_filters(
        select(TestStand),
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id, vm_id=vm_id,
    )
    stmt = stmt.order_by(TestStand.created_at.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession,
    *,
    department_id: str | None = None,
    is_active: bool | None = None,
    queue_enabled: bool | None = None,
    server_id: str | None = None,
    vm_id: str | None = None,
) -> int:
    """COUNT под теми же фильтрами, что и `list_all` — для total в pagination."""
    stmt = _apply_filters(
        select(func.count(TestStand.id)),
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id, vm_id=vm_id,
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> TestStand:
    """INSERT новой строки. commit — на caller'е."""
    obj = TestStand(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: TestStand, changes: dict) -> TestStand:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: TestStand) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
