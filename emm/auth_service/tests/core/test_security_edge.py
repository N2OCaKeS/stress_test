"""Дополнительные unit-тесты `src/core/security.py` (поверх существующего test_security.py).

Покрывает edge cases:
* `create_access_token` с большим `expires_delta` (не падает на overflow).
* `create_access_token` с отрицательным `expires_delta` → токен сразу expired.
* PAT/Bot token format и uniqueness — префикс + длина.
* `hash_opaque_token` — детерминированность + одинаковые хеши для одинаковых
  входов, разные для разных.
* `verify_password` — резистентность к битому хешу.
* JWT без `exp` или `sub` — decode даёт что есть.
"""

from datetime import timedelta

import jwt
import pytest

from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX
from src.core.security import (
    create_access_token,
    decode_access_token,
    generate_bot_token,
    generate_pat,
    generate_refresh_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)


# ── JWT exp edge cases ───────────────────────────────────────────────────────

class TestJwtExpiration:
    def test_negative_delta_means_already_expired(self):
        # JWT_LEEWAY_SECONDS=10 → нужно уйти ЗА пределы leeway, чтобы decode упал.
        token = create_access_token({"sub": "u"}, expires_delta=timedelta(seconds=-30))
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(token)

    def test_large_expires_delta_does_not_overflow(self):
        token = create_access_token({"sub": "u"}, expires_delta=timedelta(days=100 * 365))
        payload = decode_access_token(token)
        # exp где-то ~100 лет в будущем
        assert payload["exp"] > payload["iat"] + 50 * 365 * 86400

    def test_payload_unmodified_input(self):
        original = {"sub": "u", "username": "alice"}
        create_access_token(original)
        # `create_access_token` копирует — оригинал чист.
        assert "exp" not in original
        assert "iat" not in original

    def test_default_expires_delta_uses_settings(self):
        token = create_access_token({"sub": "u"})
        payload = decode_access_token(token)
        # ttl минут * 60 секунд
        from src.core.config import get_settings
        ttl = get_settings().access_token_ttl_minutes
        assert payload["exp"] - payload["iat"] == ttl * 60


# ── PAT / Bot token ──────────────────────────────────────────────────────────

class TestOpaqueTokens:
    def test_pat_has_correct_prefix(self):
        raw, prefix, _h = generate_pat()
        assert raw.startswith(PAT_PREFIX)
        assert prefix == raw[:12]
        # prefix фиксированной длины — нужен для аудита и поиска
        assert len(prefix) == 12

    def test_bot_token_has_correct_prefix(self):
        raw, prefix, _h = generate_bot_token()
        assert raw.startswith(BOT_TOKEN_PREFIX)
        assert prefix == raw[:12]
        assert len(prefix) == 12

    def test_pat_and_bot_have_distinct_prefixes(self):
        assert PAT_PREFIX != BOT_TOKEN_PREFIX

    def test_refresh_token_length_sufficient(self):
        raw, _h = generate_refresh_token()
        # token_urlsafe(48) → ~64 base64-символа
        assert len(raw) >= 50

    @pytest.mark.parametrize("factory", [generate_pat, generate_bot_token, generate_refresh_token])
    def test_each_invocation_unique(self, factory):
        raws = set()
        for _ in range(200):
            raw = factory()[0]
            assert raw not in raws
            raws.add(raw)


# ── Hash determinism ─────────────────────────────────────────────────────────

class TestHashOpaqueToken:
    def test_deterministic(self):
        assert hash_opaque_token("abc") == hash_opaque_token("abc")

    def test_different_inputs_different_hashes(self):
        assert hash_opaque_token("a") != hash_opaque_token("b")

    def test_hex_length(self):
        assert len(hash_opaque_token("x")) == 64  # SHA-256 hex


# ── Argon2 password ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_round_trip(self):
        h = hash_password("Strong123456789!")
        assert verify_password("Strong123456789!", h) is True

    def test_wrong_password_rejected(self):
        h = hash_password("Strong123456789!")
        assert verify_password("Wrong0000!", h) is False

    def test_corrupted_hash_returns_false_not_exception(self):
        assert verify_password("anything", "not-an-argon2-hash") is False

    def test_each_hash_is_unique_for_same_password(self):
        """Argon2 добавляет salt — два хеша одного пароля разные, но оба валидны."""
        h1 = hash_password("p@ss")
        h2 = hash_password("p@ss")
        assert h1 != h2
        assert verify_password("p@ss", h1) and verify_password("p@ss", h2)


# ── decode_access_token edge ─────────────────────────────────────────────────

class TestDecode:
    def test_token_without_sub_rejected(self):
        """`require=["sub"]` после hardening — token без `sub` должен падать.

        До hardening pyjwt не требовал `sub` и payload `{"foo":"bar"}` декодился
        как есть. Теперь require делает `sub` обязательным — иначе невозможно
        револидировать subject в `authorization_service.introspect`.
        """
        token = create_access_token({"foo": "bar"})
        with pytest.raises(jwt.MissingRequiredClaimError):
            decode_access_token(token)

    def test_garbage_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token("not.a.jwt")
