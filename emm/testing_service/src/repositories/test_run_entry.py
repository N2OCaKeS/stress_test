"""Сохранённый состав кампании."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models import TestRunEntry


async def list_for_run(db: AsyncSession, run_id: str) -> list[TestRunEntry]:
    stmt = (
        select(TestRunEntry)
        .where(TestRunEntry.test_run_id == run_id)
        .order_by(TestRunEntry.stand_id, TestRunEntry.test_code, TestRunEntry.id)
    )
    return list((await db.execute(stmt)).scalars())
