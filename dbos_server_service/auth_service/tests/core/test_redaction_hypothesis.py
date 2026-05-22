"""Property-based тесты для `services/redaction.py`.

Инварианты:
* `redact` идемпотентна: `redact(redact(x)) == redact(x)`;
* для любого ключа из `_PASSWORD_KEYS/_TOKEN_KEYS/...` значение в выходе —
  ровно соответствующий плейсхолдер;
* `redact` не мутирует исходный объект;
* для случайных «безопасных» ключей значения не подменяются (если они не
  выглядят как JWT/Argon2/bcrypt/opaque-token);
* JWT-форма строки → `<TOKEN>` независимо от ключа.
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings, strategies as st

from src.services.redaction import (
    _CREDENTIAL_KEYS,
    _HASH_KEYS,
    _PASSWORD_KEYS,
    _SECRET_KEYS,
    _TOKEN_KEYS,
    redact,
)


_SAFE_KEYS = (
    "username", "user_id", "name", "email", "department_id",
    "reason", "service", "action", "status", "ip", "request_id",
)


_S = settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])


# ── Идемпотентность ──────────────────────────────────────────────────────────

@_S
@given(st.dictionaries(
    keys=st.sampled_from(_SAFE_KEYS + tuple(_PASSWORD_KEYS) + tuple(_TOKEN_KEYS)),
    values=st.one_of(
        st.text(max_size=64),
        st.integers(min_value=-1000, max_value=1000),
        st.booleans(),
        st.none(),
    ),
    max_size=8,
))
def test_redact_idempotent(payload: dict):
    once = redact(payload)
    twice = redact(once)
    assert once == twice


# ── Ключи-секреты → плейсхолдер ──────────────────────────────────────────────

@_S
@given(
    sensitive_key=st.sampled_from(tuple(_PASSWORD_KEYS)),
    value=st.text(min_size=1, max_size=64),
)
def test_password_keys_always_replaced(sensitive_key: str, value: str):
    out = redact({sensitive_key: value})
    assert out[sensitive_key] == "<PASSWORD>"


@_S
@given(
    sensitive_key=st.sampled_from(tuple(_TOKEN_KEYS)),
    value=st.text(min_size=1, max_size=64),
)
def test_token_keys_always_replaced(sensitive_key: str, value: str):
    out = redact({sensitive_key: value})
    assert out[sensitive_key] == "<TOKEN>"


@_S
@given(
    sensitive_key=st.sampled_from(tuple(_SECRET_KEYS)),
    value=st.text(min_size=1, max_size=64),
)
def test_secret_keys_always_replaced(sensitive_key: str, value: str):
    out = redact({sensitive_key: value})
    assert out[sensitive_key] == "<SECRET>"


@_S
@given(
    sensitive_key=st.sampled_from(tuple(_HASH_KEYS)),
    value=st.text(min_size=1, max_size=64),
)
def test_hash_keys_always_replaced(sensitive_key: str, value: str):
    out = redact({sensitive_key: value})
    assert out[sensitive_key] == "<HASH>"


@_S
@given(
    sensitive_key=st.sampled_from(tuple(_CREDENTIAL_KEYS)),
    value=st.text(min_size=1, max_size=64),
)
def test_credential_keys_always_replaced(sensitive_key: str, value: str):
    out = redact({sensitive_key: value})
    assert out[sensitive_key] == "<CREDENTIAL>"


# ── Case-insensitivity по имени ключа ────────────────────────────────────────

@_S
@given(value=st.text(min_size=1, max_size=32))
def test_key_classification_case_insensitive(value: str):
    """`Password`, `PASSWORD`, `pAsSwOrD` — все → `<PASSWORD>`."""
    for variant in ("Password", "PASSWORD", "pAsSwOrD"):
        out = redact({variant: value})
        assert out[variant] == "<PASSWORD>"


# ── Безопасные ключи: значения не подменяются (если форма безопасная) ────────

_SAFE_VALUE = st.text(
    alphabet=string.ascii_letters + string.digits + " -_",
    min_size=0, max_size=64,
).filter(lambda s: s.count(".") < 2)  # отсекаем «3 сегмента через точку»


@_S
@given(
    safe_key=st.sampled_from(_SAFE_KEYS),
    value=_SAFE_VALUE,
)
def test_safe_keys_with_safe_values_unchanged(safe_key: str, value: str):
    out = redact({safe_key: value})
    assert out[safe_key] == value


# ── JWT-форма → <TOKEN> независимо от ключа ──────────────────────────────────

_JWT_SEGMENT = st.text(
    alphabet=string.ascii_letters + string.digits + "_-", min_size=8, max_size=32,
)


@_S
@given(
    safe_key=st.sampled_from(_SAFE_KEYS),
    seg1=_JWT_SEGMENT,
    seg2=_JWT_SEGMENT,
    seg3=_JWT_SEGMENT,
)
def test_jwt_value_redacted_regardless_of_key(safe_key, seg1, seg2, seg3):
    jwt = f"{seg1}.{seg2}.{seg3}"
    out = redact({safe_key: jwt})
    assert out[safe_key] == "<TOKEN>"


# ── Opaque PAT/bot-tokens → <TOKEN> по значению ──────────────────────────────

_OPAQUE = st.text(
    alphabet=string.ascii_letters + string.digits + "_-", min_size=12, max_size=48,
)


@_S
@given(
    prefix=st.sampled_from(("dbos_pat_", "dbos_bot_")),
    suffix=_OPAQUE,
    safe_key=st.sampled_from(_SAFE_KEYS),
)
def test_opaque_token_redacted_regardless_of_key(prefix, suffix, safe_key):
    out = redact({safe_key: f"{prefix}{suffix}"})
    assert out[safe_key] == "<TOKEN>"


# ── Immutability ─────────────────────────────────────────────────────────────

@_S
@given(st.dictionaries(
    keys=st.sampled_from(_SAFE_KEYS + tuple(_PASSWORD_KEYS)),
    values=st.text(max_size=32),
    max_size=8,
))
def test_redact_does_not_mutate_input(original: dict):
    snapshot = dict(original)
    redact(original)
    assert original == snapshot


# ── Nested structures ────────────────────────────────────────────────────────

@_S
@given(
    sensitive=st.sampled_from(tuple(_PASSWORD_KEYS)),
    value=st.text(min_size=1, max_size=32),
)
def test_nested_dict_password_redacted(sensitive, value):
    payload = {"outer": {"inner": {sensitive: value, "ok": "fine"}}}
    out = redact(payload)
    assert out["outer"]["inner"][sensitive] == "<PASSWORD>"
    assert out["outer"]["inner"]["ok"] == "fine"


@_S
@given(value=st.text(min_size=1, max_size=32))
def test_list_of_dicts_redacted(value: str):
    payload = {"items": [{"password": value}, {"name": "ok"}]}
    out = redact(payload)
    assert out["items"][0]["password"] == "<PASSWORD>"
    assert out["items"][1]["name"] == "ok"
