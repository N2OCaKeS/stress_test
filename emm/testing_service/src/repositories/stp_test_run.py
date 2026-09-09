"""StpTestRun-репозиторий — сырой CRUD против таблицы `stp_test_runs`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpTestRun


async def get_by_id(db: AsyncSession, run_id: str) -> StpTestRun | None:
    stmt = select(StpTestRun).where(StpTestRun.id == run_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_latest_for_context(
    db: AsyncSession, *, stand_id: str, os_version_id: str, mode: str, kernel: str,
) -> StpTestRun | None:
    """Самый свежий СТП-прогон под этот `(stand, RC, mode, kernel)` — используется
    событийным обновлением статуса (`services/stp_status.py`) чтобы найти, в какой
    прогон записать результат завершившегося `queue_item`."""
    stmt = (
        select(StpTestRun)
        .where(
            StpTestRun.stand_id == stand_id,
            StpTestRun.os_version_id == os_version_id,
            StpTestRun.mode == mode,
            StpTestRun.kernel == kernel,
        )
        .order_by(StpTestRun.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _apply_filters(stmt, *, stand_id: str | None, os_version_id: str | None):
    if stand_id is not None:
        stmt = stmt.where(StpTestRun.stand_id == stand_id)
    if os_version_id is not None:
        stmt = stmt.where(StpTestRun.os_version_id == os_version_id)
    return stmt


async def list_all(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    *,
    stand_id: str | None = None,
    os_version_id: str | None = None,
) -> list[StpTestRun]:
    stmt = _apply_filters(select(StpTestRun), stand_id=stand_id, os_version_id=os_version_id)
    stmt = stmt.order_by(StpTestRun.created_at.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(
    db: AsyncSession, *, stand_id: str | None = None, os_version_id: str | None = None,
) -> int:
    stmt = _apply_filters(
        select(func.count(StpTestRun.id)), stand_id=stand_id, os_version_id=os_version_id,
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> StpTestRun:
    obj = StpTestRun(**data)
    db.add(obj)
    await db.flush()
    return obj
