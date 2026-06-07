"""Зависимость DB-сессии. Подключается в endpoint'ах через Depends(get_db)."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async-сессия на один request. Закрытие через context manager."""
    async with AsyncSessionLocal() as session:
        yield session
