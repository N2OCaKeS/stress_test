"""DB engine и фабрика сессий.

Размер pool'а конфигурируется через `DB_POOL_SIZE` и `DB_MAX_OVERFLOW` —
старые хардкоды (10 / 20) живут как дефолты в `core.config.Settings`.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
