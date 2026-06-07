"""Liveness и readiness probe'ы. K8s ходит сюда с интервалом."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from src.db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness probe — сервис жив, отдаёт 200 без проверок зависимостей",
    description=(
        "Не трогает БД и не зовёт внешние сервисы. k8s liveness probe ходит "
        "сюда чтобы понять, что процесс не повис."
    ),
)
async def health() -> dict[str, str]:
    """Сервис жив. Без проверок зависимостей — только timestamp."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get(
    "/ready",
    summary="Readiness probe — БД доступна, можно принимать трафик",
    description=(
        "БД обязательна: SELECT 1 не прошёл — 500, k8s выводит pod из трафика. "
        "На следующих фазах сюда добавятся пинги Redis / loging_service / "
        "auth_service (best-effort, статус каждого в payload)."
    ),
)
async def ready() -> dict:
    """БД доступна — SELECT 1 проходит."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": "ok",
    }
