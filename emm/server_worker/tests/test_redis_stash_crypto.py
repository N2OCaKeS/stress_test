"""Unit-тесты worker-стороны envelope-шифрования Redis-stash'а.

Mirror server_service/tests/test_redis_stash_crypto.py, но для worker'ового
`AppException` (без `http_status`). Покрывает:

  * round-trip encrypt/decrypt;
  * AAD swap-attack → STASH_DECRYPT_FAILED;
  * version mismatch без ключа в env → REDIS_STASH_KEY_MISSING;
  * malformed token / без префикса / битый base64.

Кросс-сервисный round-trip покрывается deployment-конфигом (общий Secret
в k8s), не unit-тестом.
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
    assert stash_id_from_key("dbos:dispatch_creds:dcd_abc") == "dcd_abc"
    assert stash_id_from_key("nothing") == "nothing"


def test_aad_format():
    assert aad_for_redis_stash("dcd_abc") == b"redis_stash|dcd_abc"


def test_roundtrip():
    aad = aad_for_redis_stash("task_1")
    token = encrypt_stash("hello", aad=aad)
    assert token.startswith("v1$")
    assert decrypt_stash(token, aad=aad) == "hello"


def test_aad_swap_raises_decrypt_failed():
    token = encrypt_stash("hello", aad=aad_for_redis_stash("task_A"))
    with pytest.raises(AppException) as ei:
        decrypt_stash(token, aad=aad_for_redis_stash("task_B"))
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"


def test_decrypt_no_prefix():
    with pytest.raises(AppException) as ei:
        decrypt_stash("payload-no-prefix", aad=b"x")
    assert ei.value.error_code == "STASH_TOKEN_INVALID"


def test_decrypt_malformed():
    with pytest.raises(AppException) as ei:
        decrypt_stash("vXXX$abc$def", aad=b"x")
    assert ei.value.error_code == "STASH_TOKEN_MALFORMED"


def test_decrypt_unknown_version(monkeypatch):
    monkeypatch.delenv("REDIS_STASH_ENCRYPTION_KEY__v99", raising=False)
    token = "v99$AAAAAAAAAAAAAAAA$AAAAAAAAAAAA"
    with pytest.raises(AppException) as ei:
        decrypt_stash(token, aad=b"x")
    assert ei.value.error_code == "REDIS_STASH_KEY_MISSING"


def test_decrypt_corrupt_base64():
    with pytest.raises(AppException) as ei:
        decrypt_stash("v1$!!!$$$", aad=b"x")
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"


def test_decrypt_modified_ciphertext():
    aad = aad_for_redis_stash("task_X")
    token = encrypt_stash("payload", aad=aad)
    head, _ = token.rsplit("$", 1)
    corrupted = head + "$AAAAAAAA"
    with pytest.raises(AppException) as ei:
        decrypt_stash(corrupted, aad=aad)
    assert ei.value.error_code == "STASH_DECRYPT_FAILED"
