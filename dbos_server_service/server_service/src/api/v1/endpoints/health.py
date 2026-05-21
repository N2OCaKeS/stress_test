"""Health и readiness probe'ы. K8s ходит сюда с интервалом."""

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from src.db.session import engine

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness probe — сервис жив, отдаёт 200 без проверок зависимостей",
    description=(
        "Не трогает БД и не зовёт внешние сервисы. k8s liveness probe ходит "
        "сюда чтобы понять, что процесс не повис. Под audit/rate-limit middleware "
        "не попадает (исключение в `main._HEALTH_PATHS`)."
    ),
)
async def health() -> dict[str, str]:
    """Сервис жив. Без проверок зависимостей — только timestamp."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get(
    "/ready",
    summary="Readiness probe — БД доступна, можно принимать трафик",
    description=(
        "Дополнительно к liveness делает SELECT 1 в основной БД. "
        "Если коннект отвалился (pool exhausted, postgres down) — 500. "
        "k8s readiness probe; pod вне трафика пока не пройдёт."
    ),
)
async def ready() -> dict[str, str]:
    """БД доступна — SELECT 1 проходит."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}
