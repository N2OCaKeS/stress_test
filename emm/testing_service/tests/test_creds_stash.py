"""Шифрование Redis-стэша кред тестового пользователя (services/creds_stash.py)."""

from __future__ import annotations

import json
import logging

import pytest
import redis.asyncio as aioredis

from src.core.config import Settings, get_settings
from src.services import creds_stash

CREDS = {
    "test_username": "tester",
    "test_password": "s3cret-Passw0rd!",
    "test_ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nz\n-----END OPENSSH PRIVATE KEY-----\n",
}


async def _raw(key: str) -> bytes | None:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        return await client.get(key)
    finally:
        await client.aclose()


async def _put_raw(key: str, value: str) -> None:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        await client.set(key, value, ex=60)
    finally:
        await client.aclose()


async def _ttl(key: str) -> int:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        return await client.ttl(key)
    finally:
        await client.aclose()


async def test_round_trip():
    key = creds_stash.new_stash_key("item-rt")
    await creds_stash.store_creds(key, CREDS)
    assert await creds_stash.pop_creds(key) == CREDS


async def test_redis_holds_no_plaintext_and_has_ttl():
    key = creds_stash.new_stash_key("item-raw")
    await creds_stash.store_creds(key, CREDS)
    raw = await _raw(key)
    assert raw is not None
    for secret in (CREDS["test_password"], "OPENSSH PRIVATE KEY", "AAAAB3Nz", CREDS["test_username"]):
        assert secret.encode() not in raw
    assert raw.startswith(b"v1$")
    assert 0 < await _ttl(key) <= get_settings().creds_stash_ttl_seconds
    await creds_stash.pop_creds(key)


async def test_pop_is_one_shot():
    key = creds_stash.new_stash_key("item-once")
    await creds_stash.store_creds(key, CREDS)
    assert await creds_stash.pop_creds(key) == CREDS
    assert await creds_stash.pop_creds(key) is None
    assert await _raw(key) is None


async def test_missing_key_returns_none():
    assert await creds_stash.pop_creds(creds_stash.new_stash_key("item-none")) is None


async def test_nonce_is_fresh_per_entry():
    key_a = creds_stash.new_stash_key("item-a")
    key_b = creds_stash.new_stash_key("item-a")
    await creds_stash.store_creds(key_a, CREDS)
    await creds_stash.store_creds(key_b, CREDS)
    assert await _raw(key_a) != await _raw(key_b)
    await creds_stash.pop_creds(key_a)
    await creds_stash.pop_creds(key_b)


def test_entry_moved_between_keys_fails():
    token = creds_stash.encrypt_creds(CREDS, stash_key="testing:queue_creds:a:1")
    assert creds_stash.decrypt_creds(token, stash_key="testing:queue_creds:a:1") == CREDS
    with pytest.raises(creds_stash.CredsStashError, match="authentication"):
        creds_stash.decrypt_creds(token, stash_key="testing:queue_creds:b:2")


async def test_pop_of_entry_copied_under_another_key_returns_none(caplog):
    key_a = creds_stash.new_stash_key("item-a")
    key_b = creds_stash.new_stash_key("item-b")
    await creds_stash.store_creds(key_a, CREDS)
    await _put_raw(key_b, (await _raw(key_a)).decode())
    with caplog.at_level(logging.ERROR, logger="testing_service.creds_stash"):
        assert await creds_stash.pop_creds(key_b) is None
    assert "unreadable creds stash entry" in caplog.text
    assert CREDS["test_password"] not in caplog.text
    await creds_stash.pop_creds(key_a)


def test_wrong_key_fails(monkeypatch):
    settings = get_settings()
    token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    monkeypatch.setattr(settings, "creds_stash_encryption_key", "another-key-" + "x" * 40)
    with pytest.raises(creds_stash.CredsStashError, match="authentication"):
        creds_stash.decrypt_creds(token, stash_key="k")


async def test_pop_with_wrong_key_returns_none_and_consumes_entry(monkeypatch):
    settings = get_settings()
    key = creds_stash.new_stash_key("item-wrongkey")
    await creds_stash.store_creds(key, CREDS)
    monkeypatch.setattr(settings, "creds_stash_encryption_key", "another-key-" + "x" * 40)
    assert await creds_stash.pop_creds(key) is None
    assert await _raw(key) is None


def test_tampered_ciphertext_fails():
    token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    version, nonce, ct = token.split("$")
    flipped = ("A" if ct[0] != "A" else "B") + ct[1:]
    with pytest.raises(creds_stash.CredsStashError):
        creds_stash.decrypt_creds(f"{version}${nonce}${flipped}", stash_key="k")


@pytest.mark.parametrize("token", ["v1$only-two", "vX$a$b", "v1$!!!$???", "", b"\xff\xfe"])
def test_malformed_token_fails(token):
    with pytest.raises(creds_stash.CredsStashError):
        creds_stash.decrypt_creds(token, stash_key="k")


async def test_legacy_plaintext_entry_is_refused_not_crashed(caplog):
    key = creds_stash.new_stash_key("item-legacy")
    await _put_raw(key, json.dumps(CREDS))
    with caplog.at_level(logging.ERROR, logger="testing_service.creds_stash"):
        assert await creds_stash.pop_creds(key) is None
    assert "legacy unencrypted format" in caplog.text
    assert await _raw(key) is None


def test_legacy_plaintext_raises_at_low_level():
    with pytest.raises(creds_stash.CredsStashError, match="legacy"):
        creds_stash.decrypt_creds(json.dumps(CREDS), stash_key="k")


def test_unknown_key_version_fails(monkeypatch):
    settings = get_settings()
    token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    monkeypatch.setattr(settings, "creds_stash_encryption_key_version", 2)
    monkeypatch.delenv("CREDS_STASH_ENCRYPTION_KEY__v1", raising=False)
    with pytest.raises(creds_stash.CredsStashError, match="no key configured"):
        creds_stash.decrypt_creds(token, stash_key="k")


def test_rotation_reads_old_version_from_legacy_env(monkeypatch):
    settings = get_settings()
    old_key = settings.creds_stash_encryption_key
    token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    monkeypatch.setattr(settings, "creds_stash_encryption_key_version", 2)
    monkeypatch.setattr(settings, "creds_stash_encryption_key", "rotated-key-" + "y" * 40)
    monkeypatch.setenv("CREDS_STASH_ENCRYPTION_KEY__v1", old_key)
    assert creds_stash.decrypt_creds(token, stash_key="k") == CREDS
    new_token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    assert new_token.startswith("v2$")


def test_empty_key_in_dev_uses_fallback_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "creds_stash_encryption_key", "")
    with caplog.at_level(logging.WARNING, logger="testing_service.creds_stash"):
        token = creds_stash.encrypt_creds(CREDS, stash_key="k")
    assert "built-in dev key" in caplog.text
    assert creds_stash.decrypt_creds(token, stash_key="k") == CREDS
    assert CREDS["test_password"] not in token


@pytest.mark.parametrize("env", ["production", "staging"])
@pytest.mark.parametrize("key", ["", "short-key"])
def test_prod_requires_strong_key(env, key):
    settings = Settings.model_construct(app_env=env, creds_stash_encryption_key=key)
    with pytest.raises(ValueError, match="CREDS_STASH_ENCRYPTION_KEY"):
        settings._require_creds_stash_key_in_prod()


def test_prod_accepts_strong_key_and_dev_skips_check():
    strong = Settings.model_construct(app_env="production", creds_stash_encryption_key="k" * 32)
    assert strong._require_creds_stash_key_in_prod() is strong
    dev = Settings.model_construct(app_env="local", creds_stash_encryption_key="")
    assert dev._require_creds_stash_key_in_prod() is dev
