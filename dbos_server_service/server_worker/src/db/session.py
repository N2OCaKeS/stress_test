"""DB engine и фабрика сессий.

Async-only (asyncpg/psycopg3 поверх asyncpg-DSN). Engine на module-level —
один на процесс worker'а. AsyncSessionLocal — `async_sessionmaker`, отдаёт
fresh AsyncSession при каждом вызове.

`pool_pre_ping=True` — на старте каждой сессии быстрый SELECT 1 для
detection'а stale connection'ов (рестарт PostgreSQL между job'ами). При
ошибке pool автоматически discard'ит connection и берёт новый.
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

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    # `expire_on_commit=False` — после commit'а ORM-объекты сохраняют
    # данные и не делают auto-reload при next access. Нужно для outbox-
    # паттерна: `_runner.run_task` читает `task.target_server_id` уже
    # после commit'а sessионы.
    expire_on_commit=False,
)
