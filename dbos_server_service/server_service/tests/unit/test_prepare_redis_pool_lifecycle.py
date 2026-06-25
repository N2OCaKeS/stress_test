"""Pooled aioredis-клиент для bootstrap-кред prepare'а: lifespan + переиспользование.

`store_prepare_creds` раньше создавал `aioredis.from_url(...)` per-call —
burst POST /prepare ронял FD'ы и connection-budget Redis'а. Фикс: модульный
slot `worker_client._creds_redis_client`, поднимается в lifespan
`src/main.py`, закрывается на shutdown. Параллельно с pooled-introspect /
pooled-audit клиентами.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import redis.asyncio as aioredis
from src.services import worker_client


@pytest.fixture(autouse=True)
def _reset_prepare_client():
    """Между тестами обнуляем модульный slot — он живёт глобально."""
    worker_client._creds_redis_client = None
    yield
    worker_client._creds_redis_client = None


@pytest.mark.asyncio
async def test_lifespan_startup_initialises_creds_redis_client(monkeypatch):
    """После старта lifespan `_creds_redis_client` — живой `aioredis.Redis`."""
    # conftest не выставляет SERVER_WORKER_REDIS_URL — без него lifespan пропустит
    # ветку инициализации пула. Дешёвый локальный URL, aioredis.from_url ленив.
    monkeypatch.setenv("SERVER_WORKER_REDIS_URL", "redis://localhost:6379/0")
    from src.core.config import get_settings
    get_settings.cache_clear()
    async def _noop() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop)

    created: list = []
    real_from_url = aioredis.from_url

    def tracking_from_url(url: str, **kwargs):
        client = real_from_url(url, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr("src.main.aioredis.from_url", tracking_from_url)

    from src.main import create_application
    app = create_application()
    async with app.router.lifespan_context(app):
        assert worker_client._creds_redis_client is not None
        assert worker_client._creds_redis_client is created[-1]

    # После выхода из lifespan модульный slot обнулён
    assert worker_client._creds_redis_client is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_creds_redis_client(monkeypatch):
    """`aclose()` действительно вызывается на shutdown."""
    monkeypatch.setenv("SERVER_WORKER_REDIS_URL", "redis://localhost:6379/0")
    from src.core.config import get_settings
    get_settings.cache_clear()
    async def _noop() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop)

    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()

    def fake_from_url(url: str, **kwargs):
        return fake_client

    monkeypatch.setattr("src.main.aioredis.from_url", fake_from_url)

    from src.main import create_application
    app = create_application()
    async with app.router.lifespan_context(app):
        assert worker_client._creds_redis_client is fake_client

    fake_client.aclose.assert_awaited_once()
    assert worker_client._creds_redis_client is None


@pytest.mark.asyncio
async def test_lifespan_skips_pool_when_redis_url_empty(monkeypatch):
    """Если `server_worker_redis_url` пуст — клиент не создаётся вообще.

    `store_prepare_creds` в этом случае сам поднимет WORKER_REDIS_NOT_CONFIGURED;
    держать пустой пул-slot ради такого пути бессмысленно.
    """
    async def _noop() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop)

    from src.core import config as core_config
    real_get = core_config.get_settings
    real_settings = real_get()

    class _Settings:
        def __getattr__(self, name):
            if name == "server_worker_redis_url":
                return ""
            return getattr(real_settings, name)

    patched = _Settings()
    monkeypatch.setattr("src.main.get_settings", lambda: patched)

    created: list = []

    def fake_from_url(url: str, **kwargs):
        created.append(url)
        return MagicMock()

    monkeypatch.setattr("src.main.aioredis.from_url", fake_from_url)

    from src.main import create_application
    app = create_application()
    async with app.router.lifespan_context(app):
        assert worker_client._creds_redis_client is None
    assert created == []


@pytest.mark.asyncio
async def test_store_prepare_creds_reuses_pooled_client(monkeypatch):
    """`store_prepare_creds` НЕ создаёт новый `aioredis.from_url` если есть пул.

    Это и есть anti-burst инвариант: лишних TCP-открытий нет, set идёт через
    единственный pooled-клиент.
    """
    pooled = MagicMock()
    pooled.set = AsyncMock()
    pooled.aclose = AsyncMock()
    monkeypatch.setattr(worker_client, "_creds_redis_client", pooled)

    class _Settings:
        server_worker_redis_url = "redis://redis:6379/0"
        prepare_creds_ttl_seconds = 500

    monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

    from_url_calls = []

    def tracking_from_url(*args, **kwargs):
        from_url_calls.append(args)
        return MagicMock()

    monkeypatch.setattr(worker_client.aioredis, "from_url", tracking_from_url)

    await worker_client.store_prepare_creds(
        "dbos:prepare_creds:pcd_x", {"login": "boot", "password": "x"},
    )
    await worker_client.store_prepare_creds(
        "dbos:prepare_creds:pcd_y", {"login": "boot", "password": "y"},
    )

    # Pooled client использован дважды — НИ ОДНОГО нового from_url.
    assert pooled.set.await_count == 2
    assert from_url_calls == []
    # Pooled client НЕ закрывается на каждом вызове — он принадлежит lifespan.
    pooled.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_prepare_creds_fallback_when_pool_uninitialised(monkeypatch):
    """Когда пул не поднят — fallback на per-call client (для unit-тестов).

    Production-путь всегда идёт через lifespan-пул, но standalone-вызов
    `store_prepare_creds` вне приложения (legacy/unit) должен продолжать работать.
    """
    monkeypatch.setattr(worker_client, "_creds_redis_client", None)

    fake_client = MagicMock()
    fake_client.set = AsyncMock()
    fake_client.aclose = AsyncMock()

    def fake_from_url(url: str, **kwargs):
        return fake_client

    monkeypatch.setattr(worker_client.aioredis, "from_url", fake_from_url)

    class _Settings:
        server_worker_redis_url = "redis://redis:6379/0"
        prepare_creds_ttl_seconds = 500

    monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

    await worker_client.store_prepare_creds(
        "dbos:prepare_creds:pcd_z", {"login": "boot", "password": "z"},
    )

    fake_client.set.assert_awaited_once()
    # Per-call client закрывается в `finally`
    fake_client.aclose.assert_awaited_once()
