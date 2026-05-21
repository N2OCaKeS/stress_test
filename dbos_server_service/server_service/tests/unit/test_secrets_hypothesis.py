"""Property-based тесты `secrets_service` (Hypothesis).

Инварианты:
* `encrypt(x)` затем `decrypt(...)` возвращает x для любого валидного plaintext;
* токен всегда имеет ровно 2 разделителя `$` и парсимый версионный префикс `v<n>`;
* каждый encrypt одного и того же plaintext даёт разный nonce (uniqueness AES-GCM);
* любая модификация ciphertext (1 бит) ведёт к DECRYPT_FAILED;
* base64-helpers `_b64e`/`_b64d` round-trip для любых bytes.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from src.core.exceptions import AppException
from src.services import secrets_service


# Фиксированный AAD для property-based проверок; aad-семантика
# (swap-attack) тестируется отдельно в test_secrets_service.TestAADSwapAttack.
_TEST_AAD = b"test-aad-fixed"


_SLOW_OK = settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


# ── Round-trip plaintext ──────────────────────────────────────────────────────

@_SLOW_OK
@given(st.text(min_size=0, max_size=512))
def test_round_trip_arbitrary_text(plaintext: str):
    token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
    assert secrets_service.decrypt(token, aad=_TEST_AAD) == plaintext


@_SLOW_OK
@given(st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF), max_size=256))
def test_round_trip_full_unicode_range(plaintext: str):
    # Hypothesis может выдать суррогаты — отфильтруем (не валидный UTF-8).
    try:
        plaintext.encode("utf-8")
    except UnicodeEncodeError:
        return
    token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
    assert secrets_service.decrypt(token, aad=_TEST_AAD) == plaintext


# ── Token shape: ровно 2 `$` + версионный префикс ────────────────────────────

@_SLOW_OK
@given(st.text(max_size=128))
def test_token_shape_always_three_parts(plaintext: str):
    token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
    parts = token.split("$")
    assert len(parts) == 3
    assert parts[0].startswith("v")
    int(parts[0][1:])           # версия — целое число
    # Сегменты nonce и ciphertext — валидный base64url без паддинга
    assert "=" not in parts[1]
    assert "=" not in parts[2]
    secrets_service._b64d(parts[1])
    secrets_service._b64d(parts[2])


# ── Nonce uniqueness ─────────────────────────────────────────────────────────

@_SLOW_OK
@given(st.text(max_size=64))
def test_nonce_unique_across_two_encrypts(plaintext: str):
    a = secrets_service.encrypt(plaintext, aad=_TEST_AAD).split("$")[1]
    b = secrets_service.encrypt(plaintext, aad=_TEST_AAD).split("$")[1]
    assert a != b, "AES-GCM nonces must be unique for same plaintext"


# ── Bit-flip → DECRYPT_FAILED ────────────────────────────────────────────────

@_SLOW_OK
@given(st.text(min_size=1, max_size=64))
def test_any_ciphertext_bit_flip_fails(plaintext: str):
    token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
    version, nonce, ct = token.split("$", 2)
    ct_bytes = bytearray(secrets_service._b64d(ct))
    if not ct_bytes:
        return
    ct_bytes[0] ^= 0x01
    tampered = f"{version}${nonce}${secrets_service._b64e(bytes(ct_bytes))}"
    with pytest.raises(AppException) as exc:
        secrets_service.decrypt(tampered, aad=_TEST_AAD)
    assert exc.value.error_code == "DECRYPT_FAILED"


# ── base64url helpers ────────────────────────────────────────────────────────

@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.binary(min_size=0, max_size=512))
def test_b64_round_trip(raw: bytes):
    encoded = secrets_service._b64e(raw)
    assert "=" not in encoded, "unpadded by contract"
    assert secrets_service._b64d(encoded) == raw


# ── Malformed prefix → typed error ───────────────────────────────────────────

@_SLOW_OK
@given(st.text(min_size=1, max_size=32).filter(lambda s: not s.startswith("v") or "$" not in s))
def test_non_v_prefix_or_no_dollar_yields_invalid(garbage: str):
    """Любой токен, не начинающийся с 'v' или без '$' — INVALID или MALFORMED."""
    with pytest.raises(AppException) as exc:
        secrets_service.decrypt(garbage, aad=_TEST_AAD)
    assert exc.value.error_code in {
        "ENCRYPTED_TOKEN_INVALID", "ENCRYPTED_TOKEN_MALFORMED",
    }


# ── Plaintext never leaks into exception details ─────────────────────────────

@_SLOW_OK
@given(st.text(
    min_size=8,  # очень короткие plaintexts (1-2 символа) могут случайно
                 # совпадать с подстроками в именах Python-исключений
                 # (например 'I' внутри `InvalidTag`)
    max_size=64,
    alphabet=string.ascii_letters + string.digits + "!@#$%^&*",
))
def test_decrypt_failed_never_echoes_plaintext(plaintext: str):
    """tampered ciphertext: исходный plaintext не должен попасть в exception."""
    token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
    version, nonce, ct = token.split("$", 2)
    ct_bytes = bytearray(secrets_service._b64d(ct))
    ct_bytes[0] ^= 0x01
    tampered = f"{version}${nonce}${secrets_service._b64e(bytes(ct_bytes))}"
    try:
        secrets_service.decrypt(tampered, aad=_TEST_AAD)
    except AppException as exc:
        full_dump = exc.message + str(exc.details or {})
        assert plaintext not in full_dump
