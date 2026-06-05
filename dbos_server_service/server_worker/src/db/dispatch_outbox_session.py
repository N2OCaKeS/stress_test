"""Отдельный engine/session-factory для чтения `dispatch_outbox` из server_service-БД.

Worker'у нужно читать таблицу `dispatch_outbox`, которая физически живёт
в БД server_service (миграция там же). Основной `engine` в `db/session.py`
указывает на worker-БД (`dev_server_worker`) — её под dispatch_outbox
переиспользовать нельзя.

Симметрично server_service'у, который держит отдельный engine на
`dev_server_worker.tasks` (см. `server_service/src/services/worker_client.py::
_engine_factory`), здесь — engine на `dev_server_service` под чтение outbox'а.

Engine ленив: фабрика подымает его при первом обращении, чтобы worker без
сконфигурированного `SERVER_SERVICE_DATABASE_URL` стартовал (dispatch_outbox
poll просто будет no-op'ить).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Вернуть фабрику сессий для server_service-БД либо None.

    None означает «`SERVER_SERVICE_DATABASE_URL` не сконфигурирован» —
    publisher должен пропустить тик без ошибки. В local/test/dev сценариях,
    когда worker запускается в отрыве от server_service-БД, это нормальный
    путь.
    """
    global _engine, _session_factory
    settings = get_settings()
    url = settings.server_service_database_url
    if not url:
        return None
    if _engine is None:
        _engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Симметрично основному engine'у (`db/session.py`): принудительная
            # ротация idle-коннекта раз в 30 минут, до достижения idle-killer'а
            # PgBouncer / cloud-NAT.
            pool_recycle=1800,
        )
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


async def dispose() -> None:
    """Закрыть engine при graceful shutdown worker'а."""
    global _engine, _session_factory
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception:  # noqa: BLE001 — shutdown best-effort
            pass
    _engine = None
    _session_factory = None
