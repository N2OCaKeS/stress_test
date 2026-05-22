"""Зависимость на DB-сессию для FastAPI Depends."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Открыть AsyncSession на время запроса. Закрывается автоматически."""
    async with AsyncSessionLocal() as session:
        yield session
