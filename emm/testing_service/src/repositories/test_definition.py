"""TestDefinition-репозиторий — сырой CRUD против таблицы `test_definitions`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestDefinition


async def get_by_id(db: AsyncSession, test_id: str) -> TestDefinition | None:
    """SELECT по PK."""
    stmt = select(TestDefinition).where(TestDefinition.id == test_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> TestDefinition | None:
    """SELECT по UNIQUE code."""
    stmt = select(TestDefinition).where(TestDefinition.code == code)
    return (await db.execute(stmt)).scalar_one_or_none()


def _apply_filters(
    stmt,
    *,
    department_id: str | None,
    category: str | None,
    readiness: str | None,
):
    if department_id is not None:
        stmt = stmt.where(TestDefinition.department_id == department_id)
    if category is not None:
        stmt = stmt.where(TestDefinition.category == category)
    if readiness is not None:
        stmt = stmt.where(TestDefinition.readiness == readiness)
    return stmt


async def list_all(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    *,
    department_id: str | None = None,
    category: str | None = None,
    readiness: str | None = None,
) -> list[TestDefinition]:
    """Страница каталога с опциональными фильтрами, order by code."""
    stmt = _apply_filters(
        select(TestDefinition),
        department_id=department_id, category=category, readiness=readiness,
    )
    stmt = stmt.order_by(TestDefinition.code.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession,
    *,
    department_id: str | None = None,
    category: str | None = None,
    readiness: str | None = None,
) -> int:
    """COUNT под теми же фильтрами, что и `list_all` — для total в pagination."""
    stmt = _apply_filters(
        select(func.count(TestDefinition.id)),
        department_id=department_id, category=category, readiness=readiness,
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_by_pinned_stand(db: AsyncSession, stand_id: str) -> list[TestDefinition]:
    """Все тесты, закреплённые за этим стендом (`pinned_stand_id`) — вход кампании (§6.1)."""
    stmt = (
        select(TestDefinition)
        .where(TestDefinition.pinned_stand_id == stand_id)
        .order_by(TestDefinition.code.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def list_by_department_pinned(db: AsyncSession, department_id: str) -> list[TestDefinition]:
    """Тесты отдела, закреплённые за каким-либо стендом — вход СТП-генерации (§5)."""
    stmt = (
        select(TestDefinition)
        .where(
            TestDefinition.department_id == department_id,
            TestDefinition.pinned_stand_id.is_not(None),
        )
        .order_by(TestDefinition.code.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> TestDefinition:
    """INSERT новой строки. commit — на caller'е."""
    obj = TestDefinition(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: TestDefinition, changes: dict) -> TestDefinition:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: TestDefinition) -> None:
    """DELETE объекта (каскадом сносит его test_command_args). commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
