"""Engine + sessionmaker для основной БД сервиса.

Один engine на процесс. Размер pool'а из `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`
(дефолты 10/20). `pool_pre_ping` пингует коннект перед использованием,
`pool_recycle=1800` — проактивный rotate idle-сокета раз в 30 минут.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
