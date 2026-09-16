"""StpPullOperation-репозиторий — сырой CRUD против `stp_pull_operations`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpPullOperation


async def get_by_key(
    db: AsyncSession, *, department_id: str, os_version_id: str, zephyr_test_run_key: str,
) -> StpPullOperation | None:
    """UNIQUE(department_id, os_version_id, zephyr_test_run_key) — точка идемпотентности:
    повторный импорт того же рана находит эту же строку вместо дубля."""
    stmt = select(StpPullOperation).where(
        StpPullOperation.department_id == department_id,
        StpPullOperation.os_version_id == os_version_id,
        StpPullOperation.zephyr_test_run_key == zephyr_test_run_key,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> StpPullOperation:
    obj = StpPullOperation(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StpPullOperation, changes: dict) -> StpPullOperation:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
