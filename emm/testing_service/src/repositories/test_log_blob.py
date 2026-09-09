"""TestLogBlob-репозиторий — сырой текст лога, 1:1 к `test_logs` (§2.6 плана миграции)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestLogBlob


async def get_by_log_id(db: AsyncSession, log_id: str) -> TestLogBlob | None:
    """SELECT по PK (=FK на test_logs.id)."""
    stmt = select(TestLogBlob).where(TestLogBlob.log_id == log_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, log_id: str) -> TestLogBlob:
    """INSERT пустого блоба для нового лога. commit — на caller'е."""
    obj = TestLogBlob(log_id=log_id, content="")
    db.add(obj)
    await db.flush()
    return obj
