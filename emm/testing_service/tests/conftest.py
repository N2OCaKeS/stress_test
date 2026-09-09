"""Pytest fixtures для testing_service.

Волна 1 — каркас: реальный Postgres+Redis из `tests/docker-compose.test.yml`
(не моки — health/ready должны реально пингануть обе зависимости в контейнере).
"""

from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Дефолты, чтобы Settings проходил валидаторы, если тест запущен вне
# docker-compose.test.yml (там эти же значения приходят через environment).
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@localhost:5432/testing_db_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("AUTH_SERVICE_URL", "http://auth-not-used-in-skeleton-tests")
os.environ.setdefault("APP_ENV", "test")


@pytest_asyncio.fixture
async def client():
    """ASGI client поверх FastAPI app."""
    from src.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac
