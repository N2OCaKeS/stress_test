"""Pytest fixtures для secret_service.

Phase 1 — минимальный конфиг: дефолтные env'ы для Settings, ASGI client с
mock'нутой `engine.connect()` (БД ещё не нужна для health/ready теста). На
Phase 2+ сюда добавятся:

* real Postgres через `tests/docker-compose.test.yml` + per-test SAVEPOINT;
* introspect-mock (как в server_service: dict с предзаданными identity body);
* фабрики токенов разных ролей и фабрики секретов.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Минимальный набор env-переменных, чтобы Settings прошёл валидаторы. На
# Phase 1 БД реально не пингуем — `engine.connect()` мокается в фикстуре
# `client` ниже. DATABASE_URL должен быть синтаксически валидным, но коннект
# к нему не выполняется.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://secret_user:secret_password@localhost:5432/secret_db_test",
)
os.environ.setdefault("AUTH_SERVICE_URL", "http://auth-not-used-in-skeleton-tests")
os.environ.setdefault(
    "SECRET_ENCRYPTION_KEY",
    "test-secret-encryption-key-do-not-use-anywhere-else",
)
os.environ.setdefault("SECRET_ENCRYPTION_KEY_VERSION", "2")
os.environ.setdefault("HKDF_SALT_HEX", "deadbeefcafebabe0011223344556677")
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(autouse=True)
def _reset_keystore(monkeypatch, tmp_path):
    """Свежий FileKeyStore на каждый тест.

    KeyStore bootstrap'ится из env при первом обращении и кэшируется на
    процесс (`@lru_cache`). Указываем уникальный `KEYSTORE_PATH` в tmp и
    чистим кэш — каждый тест поднимает keystore из текущего env. Тесты,
    мутирующие ключи в середине, дополнительно зовут `get_keystore.cache_clear()`.
    """
    from src.core import keystore as keystore_mod

    monkeypatch.setenv("KEYSTORE_BACKEND", "file")
    monkeypatch.setenv("KEYSTORE_PATH", str(tmp_path / "secret_keystore.json"))
    keystore_mod.get_keystore.cache_clear()
    yield
    keystore_mod.get_keystore.cache_clear()


@pytest.fixture(autouse=True)
def _mock_db_connect(monkeypatch):
    """Подменяет `engine` в health-эндпоинте на объект с заглушенным `.connect()`.

    `/ready` делает `async with engine.connect() as conn: conn.execute(SELECT 1)`.
    Без живой БД это упало бы в connection-refused. AsyncEngine.connect — read-only
    атрибут, так что monkeypatch'им не сам engine, а имя `engine` в модуле
    `src.api.v1.endpoints.health` (туда оно импортировано). В Phase 1 нам важно
    проверить, что роутер, middleware и envelope-формат собраны корректно —
    реальная БД появится в Phase 2 вместе с моделями и миграциями.
    """
    from src.api.v1.endpoints import health as health_mod

    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=MagicMock())

    class _AsyncCtx:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_engine = MagicMock()
    fake_engine.connect = lambda: _AsyncCtx()
    monkeypatch.setattr(health_mod, "engine", fake_engine)
    yield


@pytest_asyncio.fixture
async def client():
    """ASGI client поверх FastAPI app."""
    from src.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac
