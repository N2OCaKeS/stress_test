"""StpTestCase-репозиторий — сырой CRUD против таблицы `stp_test_cases`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpTestCase


async def get_by_id(db: AsyncSession, case_id: str) -> StpTestCase | None:
    stmt = select(StpTestCase).where(StpTestCase.id == case_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> StpTestCase | None:
    stmt = select(StpTestCase).where(StpTestCase.code == code)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_codes(db: AsyncSession, codes: list[str]) -> list[StpTestCase]:
    """Batch-выборка по списку кодов — используется генерацией СТП (§5)."""
    if not codes:
        return []
    stmt = select(StpTestCase).where(StpTestCase.code.in_(codes))
    return list((await db.execute(stmt)).scalars())


async def list_by_ids(db: AsyncSession, ids: list[str]) -> list[StpTestCase]:
    """Batch-выборка по id — используется публикацией СТП-матрицы, где связка
    известна через `stp_cells.stp_test_case_id`, не через `code`."""
    if not ids:
        return []
    stmt = select(StpTestCase).where(StpTestCase.id.in_(ids))
    return list((await db.execute(stmt)).scalars())


def _apply_filters(stmt, *, department_id: str | None):
    if department_id is not None:
        stmt = stmt.where(StpTestCase.department_id == department_id)
    return stmt


async def list_all(
    db: AsyncSession, limit: int = 100, offset: int = 0, *, department_id: str | None = None,
) -> list[StpTestCase]:
    stmt = _apply_filters(select(StpTestCase), department_id=department_id)
    stmt = stmt.order_by(StpTestCase.code.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(db: AsyncSession, *, department_id: str | None = None) -> int:
    stmt = _apply_filters(select(func.count(StpTestCase.id)), department_id=department_id)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> StpTestCase:
    obj = StpTestCase(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StpTestCase, changes: dict) -> StpTestCase:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: StpTestCase) -> None:
    await db.delete(obj)
    await db.flush()
