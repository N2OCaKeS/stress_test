"""Engine + sessionmaker для основной БД сервиса.

Один engine на процесс. Pool 10 + overflow 20 — хватает под текущую
нагрузку. `pool_pre_ping` пингует коннект перед использованием, чтобы
не нарваться на closed-сокет после restart'а postgres.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Sessionmaker, который раздаёт `AsyncSession` под `async with`-блоки.
# `expire_on_commit=False` важно: иначе после `commit` атрибуты объекта
# протухают и любое последующее обращение даст implicit SELECT.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
