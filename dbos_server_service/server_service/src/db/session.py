"""Engine + sessionmaker для основной БД сервиса.

Один engine на процесс. Размер pool'а — `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`
(дефолты 10/20 живут в `core.config.Settings`, симметрично с
`loging_service` и `server_worker`). `pool_pre_ping` пингует коннект
перед использованием, чтобы не нарваться на closed-сокет после
restart'а postgres.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
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
