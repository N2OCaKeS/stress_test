"""Юнит-тесты: src/core/security.py — пароли, JWT, opaque-токены."""

import time
from datetime import timedelta

import jwt
import pytest

from src.core.config import get_settings
from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX
from src.core.security import (
    create_access_token,
    decode_access_token,
    generate_bot_token,
    generate_pat,
    generate_refresh_token,
    hash_opaque_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


# ── Passwords (Argon2id) ─────────────────────────────────────────────────────


def test_hash_password_returns_argon2id_hash():
    h = hash_password("CorrectHorseBatteryStaple1!")
    assert h.startswith("$argon2id$"), f"expected argon2id prefix, got: {h[:20]}"


def test_hash_password_encodes_owasp_argon2id_parameters():
    """Параметры OWASP 2023 — m=65536, t=3, p=4 — должны фигурировать в строке хеша.

    argon2-cffi кодирует параметры в самом hash-строке как `$m=N,t=N,p=N$`. Без
    явной конфигурации `PasswordHasher()` мог тихо перейти на новые дефолты
    при обновлении библиотеки и ослабить production-хеши.
    """
    h = hash_password("StrongPass123!")
    assert "$m=65536,t=3,p=4$" in h, f"expected OWASP 2023 params in hash, got: {h}"


def test_hash_password_produces_distinct_hashes_for_same_password():
    plain = "CorrectHorseBatteryStaple1!"
    assert hash_password(plain) != hash_password(plain), "salt must randomise output"


def test_verify_password_true_for_correct_password():
    h = hash_password("ZxC!12345")
    assert verify_password("ZxC!12345", h) is True


def test_verify_password_false_for_wrong_password():
    h = hash_password("ZxC!12345")
    assert verify_password("nope", h) is False


def test_verify_password_false_for_malformed_hash():
    # Не argon2-хеш — verify_password должен вернуть False, а не упасть.
    assert verify_password("anything", "not-a-real-hash") is False


# ── JWT access tokens ────────────────────────────────────────────────────────


def test_create_access_token_round_trip_carries_claims():
    payload = {
        "sub": "usr_abc123",
        "department_id": "dep_nt",
        "allowed_services": ["config_service", "server_service"],
        "service_roles": {"config_service": ["reader"]},
    }
    token = create_access_token(payload)
    decoded = decode_access_token(token)

    assert decoded["sub"] == "usr_abc123"
    assert decoded["department_id"] == "dep_nt"
    assert decoded["allowed_services"] == ["config_service", "server_service"]
    assert decoded["service_roles"] == {"config_service": ["reader"]}
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_create_access_token_embeds_iss_and_aud_from_settings():
    """`iss`/`aud` берутся из настроек и сохраняются в payload."""
    settings = get_settings()
    token = create_access_token({"sub": "u1"})
    decoded = decode_access_token(token)
    assert decoded["iss"] == settings.jwt_issuer
    assert decoded["aud"] == settings.jwt_audience


def test_create_access_token_uses_configured_ttl_by_default():
    settings = get_settings()
    token = create_access_token({"sub": "u1"})
    decoded = decode_access_token(token)
    ttl_seconds = settings.access_token_ttl_minutes * 60
    # Округлим — между iat и exp ровно ttl
    assert decoded["exp"] - decoded["iat"] == ttl_seconds


def test_create_access_token_honours_explicit_expires_delta():
    token = create_access_token({"sub": "u1"}, expires_delta=timedelta(seconds=5))
    decoded = decode_access_token(token)
    assert decoded["exp"] - decoded["iat"] == 5


def test_decode_access_token_raises_for_expired_token():
    expired = create_access_token({"sub": "u1"}, expires_delta=timedelta(seconds=-30))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_decode_access_token_raises_for_wrong_signature():
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": "u1",
            "iat": int(time.time()),
            "exp": 99999999999,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        "different-secret-32-chars-aaaaa",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)
    # На всякий случай: HS256, не HS512 — секрет тот же, но подпись иная
    assert settings.secret_key != "different-secret-32-chars-aaaaa"


def test_decode_access_token_raises_for_malformed_token():
    with pytest.raises(jwt.DecodeError):
        decode_access_token("not.a.jwt")


# ── JWT iss / aud / leeway / require ─────────────────────────────────────────


def _encode_with_secret(claims: dict) -> str:
    """Сгенерировать JWT с подписью нашим secret_key (минуя `create_access_token`).

    Нужно для негативных кейсов: подменить `iss`/`aud`, опустить `sub`, и т.п.
    """
    settings = get_settings()
    return jwt.encode(claims, settings.secret_key, algorithm="HS256")


def test_decode_access_token_rejects_wrong_audience():
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "iat": now,
        "exp": now + 60,
        "iss": settings.jwt_issuer,
        "aud": "other-service",  # ← подмена audience
    })
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(token)


def test_decode_access_token_rejects_wrong_issuer():
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "iat": now,
        "exp": now + 60,
        "iss": "other-issuer",  # ← подмена issuer
        "aud": settings.jwt_audience,
    })
    with pytest.raises(jwt.InvalidIssuerError):
        decode_access_token(token)


def test_decode_access_token_accepts_token_expired_within_leeway():
    """Токен, истёкший 5s назад, должен пройти при `leeway >= 10`.

    Это покрывает clock-skew между replicas — NTP-дрейф в пару секунд не должен
    отбивать валидный токен сразу после issue.
    """
    settings = get_settings()
    assert settings.jwt_leeway_seconds >= 10
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "iat": now - 100,
        "exp": now - 5,  # 5 секунд в прошлом → в leeway
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    decoded = decode_access_token(token)
    assert decoded["sub"] == "u1"


def test_decode_access_token_rejects_token_expired_beyond_leeway():
    """Токен, истёкший 30s назад, не проходит — leeway=10s недостаточен."""
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "iat": now - 100,
        "exp": now - 30,  # 30 секунд в прошлом → за пределами leeway
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_sub():
    """`require=["sub"]` — без `sub` токен не валиден."""
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "iat": now,
        "exp": now + 60,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_exp():
    """`require=["exp"]` — бессрочный токен не валиден."""
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "iat": now,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)


def test_decode_access_token_rejects_missing_iat():
    """`require=["iat"]` — без `iat` токен не валиден."""
    settings = get_settings()
    now = int(time.time())
    token = _encode_with_secret({
        "sub": "u1",
        "exp": now + 60,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)


def test_decode_access_token_rejects_legacy_token_without_iss_aud():
    """JWT, выписанные ДО hardening (без `aud`/`iss`), не валидны после фикса.

    Это сознательный breaking-change: access TTL короткий (10 мин), refresh
    переоформляет токен с нужными claim'ами — миграция занимает максимум одну
    TTL-окно. Долгоживущие токены (PAT/bot) — opaque, JWT не используют.
    """
    settings = get_settings()
    now = int(time.time())
    legacy = _encode_with_secret({
        "sub": "u1",
        "iat": now,
        "exp": now + 60,
        # ← НИКАКИХ iss/aud
    })
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(legacy)


# ── Refresh tokens (opaque + sha256) ─────────────────────────────────────────


def test_generate_refresh_token_returns_raw_and_hash():
    raw, h = generate_refresh_token()
    assert isinstance(raw, str) and len(raw) >= 32
    assert isinstance(h, str) and len(h) == 64  # sha256 hex


def test_generate_refresh_token_is_unique():
    pairs = {generate_refresh_token()[0] for _ in range(20)}
    assert len(pairs) == 20, "raw refresh tokens must be unique across calls"


def test_hash_refresh_token_is_deterministic():
    raw, _ = generate_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)


def test_generate_refresh_token_hash_matches_hash_refresh_token():
    raw, h = generate_refresh_token()
    assert hash_refresh_token(raw) == h


# ── Personal Access Tokens ───────────────────────────────────────────────────


def test_generate_pat_starts_with_dbos_pat_prefix():
    raw, prefix, _ = generate_pat()
    assert raw.startswith(PAT_PREFIX)
    assert PAT_PREFIX == "dbos_pat_"


def test_generate_pat_prefix_is_first_12_chars():
    raw, prefix, _ = generate_pat()
    assert prefix == raw[:12]
    assert len(prefix) == 12


def test_generate_pat_hash_is_sha256_of_raw():
    raw, _, h = generate_pat()
    assert h == hash_opaque_token(raw)
    assert len(h) == 64


def test_generate_pat_is_unique():
    raws = {generate_pat()[0] for _ in range(20)}
    assert len(raws) == 20


# ── Bot tokens ───────────────────────────────────────────────────────────────


def test_generate_bot_token_starts_with_dbos_bot_prefix():
    raw, prefix, _ = generate_bot_token()
    assert raw.startswith(BOT_TOKEN_PREFIX)
    assert BOT_TOKEN_PREFIX == "dbos_bot_"


def test_generate_bot_token_prefix_is_first_12_chars():
    raw, prefix, _ = generate_bot_token()
    assert prefix == raw[:12]
    assert len(prefix) == 12


def test_generate_bot_token_hash_is_sha256_of_raw():
    raw, _, h = generate_bot_token()
    assert h == hash_opaque_token(raw)


def test_generate_bot_token_is_unique():
    raws = {generate_bot_token()[0] for _ in range(20)}
    assert len(raws) == 20


# ── hash_opaque_token (deterministic SHA-256) ────────────────────────────────


def test_hash_opaque_token_is_deterministic():
    assert hash_opaque_token("hello") == hash_opaque_token("hello")


def test_hash_opaque_token_is_distinct_for_different_inputs():
    assert hash_opaque_token("hello") != hash_opaque_token("world")


def test_hash_opaque_token_matches_known_sha256():
    # echo -n "hello" | sha256sum
    expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert hash_opaque_token("hello") == expected
