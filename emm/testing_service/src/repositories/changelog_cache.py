"""ChangelogCache-репозиторий — одна строка на `build_version`."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ChangelogCache
from src.utils.ids import changelog_cache_id


async def get_by_build_version(db: AsyncSession, build_version: str) -> ChangelogCache | None:
    stmt = select(ChangelogCache).where(ChangelogCache.build_version == build_version)
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert(db: AsyncSession, build_version: str, response_json: dict) -> ChangelogCache:
    """Создать или обновить строку кэша на `build_version`. Обновляет `fetched_at`."""
    existing = await get_by_build_version(db, build_version)
    if existing is not None:
        existing.response_json = response_json
        existing.fetched_at = datetime.now(timezone.utc)
        await db.flush()
        return existing

    obj = ChangelogCache(
        id=changelog_cache_id(),
        build_version=build_version,
        response_json=response_json,
        fetched_at=datetime.now(timezone.utc),
    )
    db.add(obj)
    await db.flush()
    return obj
