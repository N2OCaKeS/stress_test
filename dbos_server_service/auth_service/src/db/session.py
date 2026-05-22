"""Engine и фабрика AsyncSession для SQLAlchemy."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

_settings = get_settings()

# pool_size=10 + max_overflow=20 даёт суммарный лимит 30 соединений к БД —
# нормально для одной replica auth_service'а под нагрузкой.
engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# `expire_on_commit=False` важен для FastAPI: после `db.commit()` мы продолжаем
# использовать ORM-инстансы в response-сериализации (Pydantic from_attributes).
# Без этой опции SQLAlchemy expire'ил бы атрибуты и приходилось бы refresh'ить.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
