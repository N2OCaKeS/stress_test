"""Unit-тесты envelope-шифрования Redis-stash'а.

Покрывает:

  * round-trip encrypt/decrypt с правильным AAD;
  * AAD-swap: тот же ciphertext под разным AAD → DECRYPT_FAILED (InvalidTag);
  * version mismatch: токен с неизвестной версией → REDIS_STASH_KEY_MISSING;
  * corrupt-token: битый base64 / отсутствие префикса / malformed формат;
  * `aad_for_redis_stash` / `stash_id_from_key` базовая структура.

Без сети, без Redis — только криптомодуль.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AppException
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    encrypt_stash,
    stash_id_from_key,
)


def test_stash_id_from_key():
    assert stash_id_from_key("dbos:prepare_creds:pcd_abc") == "pcd_abc"
    assert stash_id_from_key("dbos:dispatch_creds:dcd_xyz") == "dcd_xyz"
    assert stash_id_from_key("no_separator") == "no_separator"


def test_aad_for_redis_stash_format():
    assert aad_for_redis_stash("pcd_abc") == b"redis_stash|pcd_abc"


def test_encrypt_decrypt_roundtrip():
    aad = aad_for_redis_stash("stash_1")
    token = encrypt_stash("secret-payload", aad=aad)
    # Wire-формат — v<N>$nonce$ct, version 1 по тестовому setup'у.
    assert token.startswith("v1$")
    assert token.count("$") == 2
    assert decrypt_stash(token, aad=aad) == "secret-payload"


def test_encrypt_produces_unique_nonce_per_call():
    aad = aad_for_redis_stash("stash_1")
    t1 = encrypt_stash("payload", aad=aad)
    t2 = encrypt_stash("payload", aad=aad)
    # Каждый encrypt должен генерить свежий nonce — иначе AES-GCM с
    # повторённым (key, nonce) полностью ломается.
    assert t1 != t2
    assert decrypt_stash(t1, aad=aad) == "payload"
    assert decrypt_stash(t2, aad=aad) == "payload"


def test_decrypt_with_wrong_aad_raises_decrypt_failed():
    token = encrypt_stash("payload", aad=aad_for_redis_stash("stash_A"))
    with pytest.raises(AppException) as ei:
        decrypt_stash(token, aad=aad_for_redis_stash("stash_B"))
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"


def test_decrypt_token_without_version_prefix():
    with pytest.raises(AppException) as ei:
        decrypt_stash("plaintext-not-a-token", aad=b"x")
    assert ei.value.error_code == "STASH_TOKEN_INVALID"


def test_decrypt_empty_token():
    with pytest.raises(AppException) as ei:
        decrypt_stash("", aad=b"x")
    assert ei.value.error_code == "STASH_TOKEN_INVALID"


def test_decrypt_malformed_token():
    with pytest.raises(AppException) as ei:
        decrypt_stash("vNOTANUMBER$abc$def", aad=b"x")
    assert ei.value.error_code == "STASH_TOKEN_MALFORMED"


def test_decrypt_corrupt_base64():
    # Версия правильная, AES-ключ найдётся; base64 битый — STASH_DECRYPT_FAILED.
    with pytest.raises(AppException) as ei:
        decrypt_stash("v1$!!!not-base64!!!$alsobad", aad=b"x")
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"


def test_decrypt_unknown_version_missing_key(monkeypatch):
    # Подсунем токен с version=99 — ключа в env нет, поднимется REDIS_STASH_KEY_MISSING.
    monkeypatch.delenv("REDIS_STASH_ENCRYPTION_KEY__v99", raising=False)
    token = "v99$AAAAAAAAAAAAAAAA$AAAAAAAAAAAA"
    with pytest.raises(AppException) as ei:
        decrypt_stash(token, aad=b"x")
    assert ei.value.error_code == "REDIS_STASH_KEY_MISSING"


def test_decrypt_modified_ciphertext():
    aad = aad_for_redis_stash("stash_X")
    token = encrypt_stash("payload", aad=aad)
    # Поломаем хвост ciphertext'а — auth tag не сойдётся.
    head, _last_chunk = token.rsplit("$", 1)
    corrupted = head + "$AAAAAAAA"
    with pytest.raises(AppException) as ei:
        decrypt_stash(corrupted, aad=aad)
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"
