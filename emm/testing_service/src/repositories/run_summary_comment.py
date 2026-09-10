"""RunSummaryComment-репозиторий — сырой CRUD против таблицы `run_summary_comments`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import RunSummaryComment


async def get_by_test_run_id(db: AsyncSession, test_run_id: str) -> RunSummaryComment | None:
    stmt = select(RunSummaryComment).where(RunSummaryComment.test_run_id == test_run_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> RunSummaryComment:
    obj = RunSummaryComment(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: RunSummaryComment, changes: dict) -> RunSummaryComment:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
