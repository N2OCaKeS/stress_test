"""TestCommandArg-репозиторий — сырой CRUD против таблицы `test_command_args`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestCommandArg


async def get_by_id(db: AsyncSession, arg_id: str) -> TestCommandArg | None:
    """SELECT по PK."""
    stmt = select(TestCommandArg).where(TestCommandArg.id == arg_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_test(db: AsyncSession, test_id: str) -> list[TestCommandArg]:
    """Все слоты одного теста, упорядоченные по `position`."""
    stmt = (
        select(TestCommandArg)
        .where(TestCommandArg.test_id == test_id)
        .order_by(TestCommandArg.position.asc(), TestCommandArg.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def max_position(db: AsyncSession, test_id: str) -> int | None:
    """Максимальный `position` среди слотов теста — для append в конец."""
    stmt = select(func.max(TestCommandArg.position)).where(TestCommandArg.test_id == test_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> TestCommandArg:
    """INSERT нового слота. commit — на caller'е."""
    obj = TestCommandArg(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: TestCommandArg, changes: dict) -> TestCommandArg:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: TestCommandArg) -> None:
    """DELETE слота. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
