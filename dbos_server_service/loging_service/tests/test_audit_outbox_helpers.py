"""Unit-тесты приватных хелперов `audit_outbox`:

* `_resolve_actor_type` — единая точка whitelist'-резолва, чтобы и writer
  (`write_envelope_to_db`), и legacy-fallback (`main._emit_audit`) не
  разъезжались по семантике.
* `_coerce_details_keys` — рекурсивный приведёт non-string ключи к `str`.
  `EventCreate._details_shadow_keys` валидирует только `isinstance(k, str)`
  ключи; int/tuple проходили мимо guard'а и улетали в JSONB.
"""

import logging

from src.services.audit_outbox import _coerce_details_keys, _resolve_actor_type


# ── _resolve_actor_type ──────────────────────────────────────────────────────


class TestResolveActorType:
    def test_known_passes_through(self):
        for at in ("user", "bot", "service", "anonymous", "oauth_client"):
            assert _resolve_actor_type(at) == at

    def test_none_falls_back_to_anonymous(self):
        assert _resolve_actor_type(None) == "anonymous"

    def test_none_does_not_warn(self, caplog):
        # `None` — штатный 401/неаутентифицированный путь (identity ещё не
        # получен middleware'ом). Это легитимный anonymous, WARNING тут
        # был бы шумом в SIEM.
        with caplog.at_level(logging.WARNING, logger="src.services.audit_outbox"):
            assert _resolve_actor_type(None) == "anonymous"
        assert caplog.records == []

    def test_unknown_falls_back_to_anonymous(self):
        # Future subject_type от auth_service, который ещё не разрешён
        # `VALID_ACTOR_TYPES`, не должен подмешиваться в user-агрегаты.
        assert _resolve_actor_type("admin") == "anonymous"
        assert _resolve_actor_type("") == "anonymous"
        assert _resolve_actor_type("USER") == "anonymous"  # case-sensitive

    def test_unknown_non_none_warns(self, caplog):
        # Непустой actor_type вне whitelist'а — действительно неожиданный
        # случай (новый subject_type или мусор от call-site). Его хочется
        # увидеть в warning-логах.
        with caplog.at_level(logging.WARNING, logger="src.services.audit_outbox"):
            assert _resolve_actor_type("admin") == "anonymous"
        assert any("unknown actor_type" in r.message for r in caplog.records)


# ── _coerce_details_keys ─────────────────────────────────────────────────────


class TestCoerceDetailsKeys:
    def test_string_keys_passthrough(self):
        src = {"a": 1, "b": {"c": [1, 2]}}
        assert _coerce_details_keys(src) == src

    def test_int_keys_become_str(self):
        src = {1: "x", 2: {3: "y"}}
        out = _coerce_details_keys(src)
        assert out == {"1": "x", "2": {"3": "y"}}

    def test_tuple_keys_become_str(self):
        out = _coerce_details_keys({(1, 2): "v"})
        assert "(1, 2)" in out
        assert out["(1, 2)"] == "v"

    def test_lists_recurse(self):
        src = {"items": [{1: "a"}, {"k": [{2: "b"}]}]}
        out = _coerce_details_keys(src)
        assert out == {"items": [{"1": "a"}, {"k": [{"2": "b"}]}]}

    def test_non_container_passthrough(self):
        assert _coerce_details_keys("scalar") == "scalar"
        assert _coerce_details_keys(42) == 42
        assert _coerce_details_keys(None) is None

    def test_input_not_mutated(self):
        src = {1: "x", "nested": {2: "y"}}
        snapshot = {1: "x", "nested": {2: "y"}}
        _coerce_details_keys(src)
        assert src == snapshot
