"""Engine и фабрика AsyncSession для SQLAlchemy.

Размер pool'а конфигурируется через `DB_POOL_SIZE` и `DB_MAX_OVERFLOW`
(дефолты 10/20 живут в `core.config.Settings`, симметрично с
`loging_service`, `server_service` и `server_worker`). `pool_pre_ping`
пингует коннект перед использованием, чтобы не нарваться на closed-сокет
после restart'а postgres. `pool_recycle` принудительно ротирует коннект
старше N секунд — закрывает PgBouncer/cloud-proxy idle-killer окно, когда
proxy молча перестаёт отвечать после истечения своего timeout'а
(`pool_pre_ping` ловит уже-закрытый сокет, но не помогает на «полузависших»
соединениях).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_recycle=_settings.db_pool_recycle_seconds,
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
