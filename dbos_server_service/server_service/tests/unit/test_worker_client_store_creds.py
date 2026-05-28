"""Edge cases для worker_client.store_prepare_creds.

Дополняет test_prepare_creds_store.py и test_prepare_redis_pool_lifecycle.py.
Фокус на ветках, не покрытых ранее:
* WORKER_REDIS_NOT_CONFIGURED — пустой redis URL при отсутствии пула.
* store_prepare_creds с пустым URL и без pool → ServiceUnavailableError.
* TTL прокидывается правильно из settings в pooled и fallback путях.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import ServiceUnavailableError
from src.services import worker_client


@pytest.fixture(autouse=True)
def _reset_prepare_client():
    original = worker_client._prepare_redis_client
    yield
    worker_client._prepare_redis_client = original


class TestStorePrepareCredsNoUrl:
    @pytest.mark.asyncio
    async def test_empty_redis_url_raises_worker_redis_not_configured(self, monkeypatch):
        """Нет URL и нет пула → WORKER_REDIS_NOT_CONFIGURED (не 500)."""
        monkeypatch.setattr(worker_client, "_prepare_redis_client", None)

        class _Settings:
            server_worker_redis_url = ""
            prepare_creds_ttl_seconds = 300

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        with pytest.raises(ServiceUnavailableError) as exc:
            await worker_client.store_prepare_creds(
                "dbos:prepare_creds:pcd_test", {"login": "x", "password": "y"},
            )
        assert exc.value.error_code == "WORKER_REDIS_NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_none_redis_url_raises_worker_redis_not_configured(self, monkeypatch):
        monkeypatch.setattr(worker_client, "_prepare_redis_client", None)

        class _Settings:
            server_worker_redis_url = None
            prepare_creds_ttl_seconds = 300

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        with pytest.raises(ServiceUnavailableError) as exc:
            await worker_client.store_prepare_creds(
                "dbos:prepare_creds:pcd_test2", {"login": "x"},
            )
        assert exc.value.error_code == "WORKER_REDIS_NOT_CONFIGURED"


class TestStorePrepareCredsTTL:
    @pytest.mark.asyncio
    async def test_pooled_path_uses_settings_ttl(self, monkeypatch):
        """TTL из settings передаётся в SET через pooled client."""
        pooled = MagicMock()
        pooled.set = AsyncMock()
        monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

        class _Settings:
            server_worker_redis_url = "redis://redis:6379/0"
            prepare_creds_ttl_seconds = 1234

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        creds = {"bootstrap_login": "boot", "bootstrap_password": "s3cret"}
        key = "dbos:prepare_creds:pcd_ttl_test"
        await worker_client.store_prepare_creds(key, creds)

        pooled.set.assert_awaited_once_with(
            key, json.dumps(creds), ex=1234,
        )

    @pytest.mark.asyncio
    async def test_fallback_path_uses_settings_ttl(self, monkeypatch):
        """TTL прокидывается и в per-call fallback client."""
        monkeypatch.setattr(worker_client, "_prepare_redis_client", None)

        fake_client = MagicMock()
        fake_client.set = AsyncMock()
        fake_client.aclose = AsyncMock()

        import redis.asyncio as aioredis
        monkeypatch.setattr(aioredis, "from_url", lambda url, **kw: fake_client)
        monkeypatch.setattr(worker_client.aioredis, "from_url", lambda url, **kw: fake_client)

        class _Settings:
            server_worker_redis_url = "redis://redis:6379/0"
            prepare_creds_ttl_seconds = 777

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        creds = {"bootstrap_login": "b", "bootstrap_password": "p"}
        key = "dbos:prepare_creds:pcd_fallback_ttl"
        await worker_client.store_prepare_creds(key, creds)

        fake_client.set.assert_awaited_once_with(
            key, json.dumps(creds), ex=777,
        )
        # Per-call client закрывается.
        fake_client.aclose.assert_awaited_once()


class TestStorePrepareCredsJsonSerialization:
    @pytest.mark.asyncio
    async def test_creds_serialized_as_json(self, monkeypatch):
        """Значение в Redis — JSON-строка, чтобы worker мог его прочитать."""
        pooled = MagicMock()
        pooled.set = AsyncMock()
        monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

        class _Settings:
            server_worker_redis_url = "redis://redis:6379/0"
            prepare_creds_ttl_seconds = 300

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        creds = {"bootstrap_login": "admin", "bootstrap_password": "qwerty123"}
        await worker_client.store_prepare_creds("key", creds)

        call_args = pooled.set.call_args
        stored_value = call_args[0][1]
        # Значение должно быть строкой, которая десериализуется обратно.
        assert json.loads(stored_value) == creds

    @pytest.mark.asyncio
    async def test_creds_with_special_chars_serialized_correctly(self, monkeypatch):
        """Спецсимволы в пароле не ломают сериализацию."""
        pooled = MagicMock()
        pooled.set = AsyncMock()
        monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

        class _Settings:
            server_worker_redis_url = "redis://redis:6379/0"
            prepare_creds_ttl_seconds = 300

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        creds = {
            "bootstrap_login": "root",
            "bootstrap_password": "P@$$w0rd!#%&*()\"';:",
        }
        await worker_client.store_prepare_creds("key2", creds)

        stored_value = pooled.set.call_args[0][1]
        assert json.loads(stored_value) == creds
