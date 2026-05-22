"""DB engine и фабрика сессий.

# HACK: `pool_size=10` / `max_overflow=20` хардкод без env-конфига. Не
# критично сейчас, но для нагрузочной настройки имеет смысл вынести в ENV.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
