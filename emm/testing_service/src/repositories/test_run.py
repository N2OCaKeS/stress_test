"""TestRun-репозиторий — сырой CRUD против таблицы `test_runs`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestRun


async def get_by_id(db: AsyncSession, run_id: str) -> TestRun | None:
    """SELECT по PK."""
    stmt = select(TestRun).where(TestRun.id == run_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id_for_update(db: AsyncSession, run_id: str) -> TestRun | None:
    """SELECT ... FOR UPDATE по PK — сериализует конкурентный пересчёт статуса."""
    stmt = select(TestRun).where(TestRun.id == run_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def _apply_filters(
    stmt,
    *,
    department_id: str | None,
    status: str | None,
    final: bool | None,
):
    if department_id is not None:
        stmt = stmt.where(TestRun.department_id == department_id)
    if status is not None:
        stmt = stmt.where(TestRun.status == status)
    if final is not None:
        stmt = stmt.where(TestRun.final == final)
    return stmt


async def list_all(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    *,
    department_id: str | None = None,
    status: str | None = None,
    final: bool | None = None,
) -> list[TestRun]:
    """Страница кампаний с опциональными фильтрами, самые свежие первыми."""
    stmt = _apply_filters(
        select(TestRun), department_id=department_id, status=status, final=final,
    )
    stmt = stmt.order_by(TestRun.created_at.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession,
    *,
    department_id: str | None = None,
    status: str | None = None,
    final: bool | None = None,
) -> int:
    """COUNT под теми же фильтрами, что и `list_all` — для total в pagination."""
    stmt = _apply_filters(
        select(func.count(TestRun.id)), department_id=department_id, status=status, final=final,
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> TestRun:
    """INSERT новой строки. commit — на caller'е."""
    obj = TestRun(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: TestRun, changes: dict) -> TestRun:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
