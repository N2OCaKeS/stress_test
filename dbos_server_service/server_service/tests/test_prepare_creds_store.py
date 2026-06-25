"""Тесты ephemeral-хранилища bootstrap-кред prepare'а в Redis.

`worker_client.store_prepare_creds` кладёт креды под одноразовый ключ с TTL —
в task-payload едет только ссылка. Проверяем: имя ключа, что значение —
envelope-зашифрованный JSON (раунд-трип через `decrypt_stash`), что TTL
берётся из конфига, и что незаданный `SERVER_WORKER_REDIS_URL` даёт
WORKER_REDIS_NOT_CONFIGURED.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import ServiceUnavailableError
from src.services import worker_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)


def test_prepare_creds_key_format():
    assert worker_client.prepare_creds_key("pcd_abc") == "dbos:prepare_creds:pcd_abc"


class _FakeSettings:
    def __init__(self, redis_url="redis://redis:6379/0", ttl=900):
        self.server_worker_redis_url = redis_url
        self.prepare_creds_ttl_seconds = ttl


async def test_store_writes_creds_with_ttl(monkeypatch):
    fake_client = MagicMock()
    fake_client.set = AsyncMock()
    fake_client.aclose = AsyncMock()
    from_url = MagicMock(return_value=fake_client)

    # Conftest autouse-stub подкладывает pooled MagicMock; чтобы протестировать
    # per-call fallback-путь (aioredis.from_url), сбрасываем pool явно.
    monkeypatch.setattr(worker_client, "_creds_redis_client", None)
    monkeypatch.setattr(worker_client, "get_settings", lambda: _FakeSettings(ttl=600))
    monkeypatch.setattr(worker_client.aioredis, "from_url", from_url)

    key = "dbos:prepare_creds:pcd_x"
    creds = {"bootstrap_login": "boot", "bootstrap_password": "S3cret"}
    await worker_client.store_prepare_creds(key, creds)

    fake_client.set.assert_awaited_once()
    call = fake_client.set.await_args
    assert call.args[0] == key
    stored = call.args[1]
    # В Redis должен лежать envelope-token, а не plaintext-JSON. Префикс
    # `v<N>$` гарантирует, что это encryption-wire-формат — даже если
    # creds-словарь однажды переедет в hex/base64, plaintext в Redis
    # пробиться не должен.
    assert isinstance(stored, str) and stored.startswith("v1$")
    assert "S3cret" not in stored
    decrypted = decrypt_stash(
        stored, aad=aad_for_redis_stash(stash_id_from_key(key)),
    )
    assert json.loads(decrypted) == creds
    assert call.kwargs["ex"] == 600
    fake_client.aclose.assert_awaited_once()


async def test_store_requires_redis_url(monkeypatch):
    monkeypatch.setattr(
        worker_client, "get_settings", lambda: _FakeSettings(redis_url=""),
    )
    with pytest.raises(ServiceUnavailableError) as ei:
        await worker_client.store_prepare_creds("k", {"a": "b"})
    assert ei.value.error_code == "WORKER_REDIS_NOT_CONFIGURED"
