"""Property-based тесты `utils/ids.py` и `core/security` token-helpers.

Инварианты:
* Каждый фабричный *_id() возвращает строку вида `<prefix><32 hex>`;
* префиксы фиксированы (стабильны для миграций / БД-конвенций);
* 5000 вызовов одной фабрики дают 5000 уникальных id (uniqueness over many);
* `_new_id(prefix)` сохраняет указанный prefix для любой ASCII-строки;
* `hash_opaque_token` детерминирован и hex-длина = 64.
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings, strategies as st

from src.core.security import hash_opaque_token
from src.utils import ids


_S = settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])


_FACTORIES = [
    (ids.user_id, "usr_"),
    (ids.department_id, "dep_"),
    (ids.session_id, "ses_"),
    (ids.pat_id, "pat_"),
    (ids.bot_id, "bot_"),
    (ids.bot_token_id, "btk_"),
    (ids.ban_id, "ban_"),
    (ids.oauth_client_id, "cli_"),
    (ids.oauth_code_id, "oac_"),
    (ids.service_role_def_id, "srd_"),
    (ids.group_id, "grp_"),
    (ids.group_membership_id, "gms_"),
    (ids.group_service_access_id, "gsa_"),
    (ids.group_service_role_id, "gsr_"),
    (ids.bot_service_role_id, "bsr_"),
]


# ── Format invariant (parametrized by Hypothesis over factories) ─────────────

@_S
@given(idx=st.integers(min_value=0, max_value=len(_FACTORIES) - 1))
def test_factory_returns_correct_shape(idx: int):
    factory, prefix = _FACTORIES[idx]
    val = factory()
    assert val.startswith(prefix)
    suffix = val[len(prefix):]
    assert len(suffix) == 32
    assert all(c in "0123456789abcdef" for c in suffix)


# ── Uniqueness over many samples ─────────────────────────────────────────────

def test_user_id_unique_over_5000():
    seen = {ids.user_id() for _ in range(5000)}
    assert len(seen) == 5000


def test_bot_service_role_id_unique_over_5000():
    seen = {ids.bot_service_role_id() for _ in range(5000)}
    assert len(seen) == 5000


def test_cross_factory_ids_dont_collide():
    """ID разных фабрик не должны совпадать (разные префиксы)."""
    samples = []
    for factory, _ in _FACTORIES:
        samples.extend(factory() for _ in range(100))
    assert len(set(samples)) == len(samples)


# ── _new_id preserves arbitrary prefix ───────────────────────────────────────

_PREFIX_ALPHABET = string.ascii_letters + string.digits + "_-."


@_S
@given(prefix=st.text(alphabet=_PREFIX_ALPHABET, min_size=0, max_size=16))
def test_new_id_preserves_prefix(prefix: str):
    val = ids._new_id(prefix)
    assert val.startswith(prefix)
    suffix = val[len(prefix):]
    assert len(suffix) == 32
    assert all(c in "0123456789abcdef" for c in suffix)


@_S
@given(prefix=st.text(alphabet=_PREFIX_ALPHABET, min_size=1, max_size=8))
def test_new_id_uniqueness_per_prefix(prefix: str):
    samples = {ids._new_id(prefix) for _ in range(200)}
    assert len(samples) == 200


# ── hash_opaque_token determinism + hex length ───────────────────────────────

@_S
@given(value=st.text(max_size=128))
def test_hash_opaque_token_deterministic(value: str):
    assert hash_opaque_token(value) == hash_opaque_token(value)


@_S
@given(value=st.text(max_size=128))
def test_hash_opaque_token_length_64_hex(value: str):
    h = hash_opaque_token(value)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


@_S
@given(a=st.text(max_size=64), b=st.text(max_size=64))
def test_hash_opaque_token_distinguishes_different_inputs(a: str, b: str):
    """Sanity: разные входы → разные хеши (за исключением случая a == b)."""
    if a == b:
        assert hash_opaque_token(a) == hash_opaque_token(b)
    else:
        assert hash_opaque_token(a) != hash_opaque_token(b)
