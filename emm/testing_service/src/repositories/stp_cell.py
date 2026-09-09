"""StpCell-репозиторий — сырой CRUD против таблицы `stp_cells`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpCell


async def get_by_id(db: AsyncSession, cell_id: str) -> StpCell | None:
    stmt = select(StpCell).where(StpCell.id == cell_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_case_and_run(
    db: AsyncSession, *, stp_test_case_id: str, stp_test_run_id: str,
) -> StpCell | None:
    stmt = select(StpCell).where(
        StpCell.stp_test_case_id == stp_test_case_id,
        StpCell.stp_test_run_id == stp_test_run_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_by_run(db: AsyncSession, stp_test_run_id: str) -> list[StpCell]:
    stmt = select(StpCell).where(StpCell.stp_test_run_id == stp_test_run_id)
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> StpCell:
    obj = StpCell(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StpCell, changes: dict) -> StpCell:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
