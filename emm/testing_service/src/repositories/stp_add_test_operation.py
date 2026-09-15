"""StpAddTestOperation-репозиторий — сырой CRUD против `stp_add_test_operations`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpAddTestOperation


async def get_by_id(db: AsyncSession, op_id: str) -> StpAddTestOperation | None:
    stmt = select(StpAddTestOperation).where(StpAddTestOperation.id == op_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_test_and_run(
    db: AsyncSession, *, test_definition_id: str, stp_test_run_id: str,
) -> StpAddTestOperation | None:
    """UNIQUE(test_definition_id, stp_test_run_id) — точка идемпотентности
    операции: повторный вызов сервиса на ту же пару находит эту же строку."""
    stmt = select(StpAddTestOperation).where(
        StpAddTestOperation.test_definition_id == test_definition_id,
        StpAddTestOperation.stp_test_run_id == stp_test_run_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> StpAddTestOperation:
    obj = StpAddTestOperation(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StpAddTestOperation, changes: dict) -> StpAddTestOperation:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
