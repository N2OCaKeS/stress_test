"""TestStand-репозиторий — сырой CRUD против таблицы `test_stands`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestStand


async def get_by_id(db: AsyncSession, stand_id: str) -> TestStand | None:
    """SELECT по PK."""
    stmt = select(TestStand).where(TestStand.id == stand_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_server_id(db: AsyncSession, server_id: str) -> TestStand | None:
    """SELECT по UNIQUE server_id."""
    stmt = select(TestStand).where(TestStand.server_id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _apply_filters(
    stmt,
    *,
    department_id: str | None,
    is_active: bool | None,
    queue_enabled: bool | None,
    server_id: str | None = None,
):
    if department_id is not None:
        stmt = stmt.where(TestStand.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(TestStand.is_active == is_active)
    if queue_enabled is not None:
        stmt = stmt.where(TestStand.queue_enabled == queue_enabled)
    if server_id is not None:
        stmt = stmt.where(TestStand.server_id == server_id)
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
) -> list[TestStand]:
    """Страница стендов с опциональными фильтрами, order by created_at."""
    stmt = _apply_filters(
        select(TestStand),
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id,
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
) -> int:
    """COUNT под теми же фильтрами, что и `list_all` — для total в pagination."""
    stmt = _apply_filters(
        select(func.count(TestStand.id)),
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id,
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
