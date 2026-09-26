"""TestStep-репозиторий — сырой CRUD против таблицы `test_steps`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestStep


async def get_by_id(db: AsyncSession, step_id: str) -> TestStep | None:
    """SELECT по PK."""
    return (await db.execute(select(TestStep).where(TestStep.id == step_id))).scalar_one_or_none()


async def list_by_test(db: AsyncSession, test_id: str) -> list[TestStep]:
    """Шаги теста по порядку `position`."""
    stmt = (
        select(TestStep)
        .where(TestStep.test_id == test_id)
        .order_by(TestStep.position.asc(), TestStep.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def first_by_tests(db: AsyncSession, test_ids: list[str]) -> dict[str, TestStep]:
    """Первый шаг каждого теста из списка — одним запросом (ответ API каталога)."""
    if not test_ids:
        return {}
    stmt = (
        select(TestStep)
        .where(TestStep.test_id.in_(test_ids))
        .order_by(TestStep.test_id, TestStep.position.asc(), TestStep.id.asc())
        .distinct(TestStep.test_id)
    )
    return {step.test_id: step for step in (await db.execute(stmt)).scalars()}


async def max_position(db: AsyncSession, test_id: str) -> int | None:
    """Наибольший `position` среди шагов теста — для добавления в конец."""
    stmt = select(func.max(TestStep.position)).where(TestStep.test_id == test_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> TestStep:
    """INSERT шага. commit — на caller'е."""
    obj = TestStep(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: TestStep, changes: dict) -> TestStep:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: TestStep) -> None:
    """DELETE шага (слоты — каскадом). commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
