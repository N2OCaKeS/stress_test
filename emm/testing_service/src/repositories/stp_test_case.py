"""StpTestCase-репозиторий — сырой CRUD против таблицы `stp_test_cases`."""

from sqlalchemy import func, or_, select
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


async def get_by_zephyr_id(db: AsyncSession, zephyr_id: str) -> StpTestCase | None:
    """Обратный к `get_by_code` поиск — по Zephyr-ключу, а не по `code`. Нужен
    §D8 (`services/stp_pull_from_life.py`): импорт из Zephyr знает только
    `testCaseKey`, локального `test_definitions.code` у него может не быть
    вовсе (тест заведён вне EMM). `zephyr_id` не UNIQUE на уровне схемы —
    при дубле забирается первая найденная строка."""
    stmt = select(StpTestCase).where(StpTestCase.zephyr_id == zephyr_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_zephyr_ids(db: AsyncSession, zephyr_ids: list[str]) -> list[StpTestCase]:
    """Batch-версия `get_by_zephyr_id` — используется предпросмотром импорта
    (§D8), чтобы не гонять по одному запросу на тест-кейс Zephyr-рана."""
    if not zephyr_ids:
        return []
    stmt = select(StpTestCase).where(StpTestCase.zephyr_id.in_(zephyr_ids))
    return list((await db.execute(stmt)).scalars())


async def list_by_ids(db: AsyncSession, ids: list[str]) -> list[StpTestCase]:
    """Batch-выборка по id — используется публикацией СТП-матрицы, где связка
    известна через `stp_cells.stp_test_case_id`, не через `code`."""
    if not ids:
        return []
    stmt = select(StpTestCase).where(StpTestCase.id.in_(ids))
    return list((await db.execute(stmt)).scalars())


def _apply_filters(stmt, *, department_id: str | None, include_unscoped: bool = False):
    if department_id is not None:
        if include_unscoped:
            # Кейсы без владельца-отдела платформенные и видны всем — тот же
            # nullable-скоуп, что у `test_definitions`.
            stmt = stmt.where(
                or_(
                    StpTestCase.department_id == department_id,
                    StpTestCase.department_id.is_(None),
                )
            )
        else:
            stmt = stmt.where(StpTestCase.department_id == department_id)
    return stmt


async def list_all(
    db: AsyncSession, limit: int = 100, offset: int = 0, *,
    department_id: str | None = None, include_unscoped: bool = False,
) -> list[StpTestCase]:
    stmt = _apply_filters(
        select(StpTestCase), department_id=department_id, include_unscoped=include_unscoped,
    )
    stmt = stmt.order_by(StpTestCase.code.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession, *, department_id: str | None = None, include_unscoped: bool = False,
) -> int:
    stmt = _apply_filters(
        select(func.count(StpTestCase.id)),
        department_id=department_id, include_unscoped=include_unscoped,
    )
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
