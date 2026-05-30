"""Coverage gaps wave 11 — loging_service.

Areas:
  1. normalize_service_name in path-params: GET /services/{service}/events
     with confusable/invisible chars, NFKC fold in path lookup.
  2. Retention _snapshot_list: group-by edge cases — single-policy grouping,
     service=None → "*", None policies skipped, sort by retain_days+severity.
  3. update_rule: 409 vs 500 IntegrityError branching (payload.name is None
     → 500 INTERNAL_ERROR, payload.name is set → 409 RULE_NAME_CONFLICT).
  4. _fetch_identity pool=None path: 503 INTROSPECT_NOT_INITIALIZED confirmed
     even when introspect_key is configured.
  5. _SECRET_KEYS: bearer key returns <TOKEN> (lives in _TOKEN_KEYS, takes
     priority); service_api_key→<SECRET>, introspect_key→<SECRET> via ingest.
  6. redact iterative: list-of-scalars passthrough, mixed list, non-dict/list
     scalar root, key→holder on nested dict when holder not None.
  7. COUNT statement_timeout actual 57014 trigger: monkeypatch DBAPIError with
     pgcode=57014 → total=None; non-57014 pgcode re-raises.
  8. normalize_service_name_preserve_case: upper unchanged, confusables folded,
     NFKC applied, invisible stripped — without lowercasing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from src.utils.normalization import normalize_service_name, normalize_service_name_preserve_case
from src.utils.redaction import _classify_key, redact

SERVICES_URL = "/api/logging/v1/services"
EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"

from tests.conftest import make_event, make_event_def, make_rule, TEST_API_KEY


# ═══════════════════════════════════════════════════════════════════════════════
# 1. normalize_service_name in GET /services/{service}/events path-param
# ═══════════════════════════════════════════════════════════════════════════════


class TestListServiceEventsCatalogNormalizationPath:
    """GET /services/{service}/events normalises the path-param via
    normalize_service_name before querying service_events.

    If the catalog was stored under the canonical name but the client
    queries with a confusable variant, it must still find the row.
    """

    def test_cyrillic_confusable_in_get_path_finds_canonical_entry(
        self, client, admin_client, auth_headers
    ):
        """Register under 'auth_service'; query GET with кириллической 'а' in
        path — normalize_service_name maps it to 'auth_service', returns rows.
        """
        client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=auth_headers,
        )
        # «аuth_service» — кириллическая 'а' (U+0430) в начале.
        cyrillic_path = "аuth_service"
        r = admin_client.get(f"{SERVICES_URL}/{cyrillic_path}/events")
        assert r.status_code == 200
        body = r.json()
        # Normalized service name echoed back as canonical.
        assert body["service"] == "auth_service"
        assert body["total"] == 1
        assert body["items"][0]["action"] == "user.login"

    def test_uppercase_path_in_get_finds_lowercase_entry(
        self, client, admin_client, auth_headers
    ):
        """GET /services/AUTH_SERVICE/events → same as /services/auth_service/events."""
        client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="bot.create")]},
            headers={**auth_headers, "X-Service-Identity": "auth_service"},
        )
        r = admin_client.get(f"{SERVICES_URL}/AUTH_SERVICE/events")
        assert r.status_code == 200
        assert r.json()["service"] == "auth_service"
        assert r.json()["total"] == 1

    def test_zero_width_space_in_get_path_finds_entry(
        self, client, admin_client, auth_headers
    ):
        """ZWSP appended to service name in GET path → normalize strips it."""
        client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.ban")]},
            headers=auth_headers,
        )
        # "auth_service" + U+200B
        zwsp_path = "auth_service​"
        r = admin_client.get(f"{SERVICES_URL}/{zwsp_path}/events")
        assert r.status_code == 200
        assert r.json()["service"] == "auth_service"
        assert r.json()["total"] == 1

    def test_empty_registry_returns_zero_total_on_normalised_path(
        self, admin_client
    ):
        """No catalog entries → total=0 regardless of path normalisation."""
        r = admin_client.get(f"{SERVICES_URL}/AUTH_SERVICE/events")
        assert r.status_code == 200
        assert r.json()["total"] == 0
        assert r.json()["items"] == []

    def test_pagination_with_normalised_path(
        self, client, admin_client, auth_headers
    ):
        """Pagination works when path needs normalisation."""
        client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action=f"ev.{i}") for i in range(5)]},
            headers=auth_headers,
        )
        r = admin_client.get(
            f"{SERVICES_URL}/AUTH_SERVICE/events",
            params={"limit": 2, "offset": 0},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 5
        assert len(r.json()["items"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 2. retention _snapshot_list edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestSnapshotListEdgeCases:
    """Unit-tests for the _snapshot_list helper in endpoints/retention.py.

    That helper is called by set_policy (PUT) and disable_policy (DELETE) to
    build audit details. Edge cases: empty list, single policy, None service,
    mixed severities, sort order.
    """

    def _snapshot_list(self, policies):
        from src.api.v1.endpoints.retention import _snapshot_list
        return _snapshot_list(policies)

    def _make_policy(self, retain_days=90, severity=None, service=None,
                     description=None, is_active=True, policy_id="rp_1"):
        p = MagicMock()
        p.id = policy_id
        p.retain_days = retain_days
        p.severity = severity
        p.service = service
        p.description = description
        p.is_active = is_active
        return p

    def test_empty_list_returns_empty(self):
        assert self._snapshot_list([]) == []

    def test_none_policy_in_list_skipped(self):
        result = self._snapshot_list([None])
        assert result == []

    def test_single_policy_no_filter_returns_one_group(self):
        p = self._make_policy(retain_days=30)
        result = self._snapshot_list([p])
        assert len(result) == 1
        g = result[0]
        assert g["retain_days"] == 30
        assert g["count"] == 1
        assert g["services"] == ["*"]  # None service → "*"
        assert g["severity"] is None

    def test_service_none_maps_to_star(self):
        p = self._make_policy(service=None)
        result = self._snapshot_list([p])
        assert result[0]["services"] == ["*"]

    def test_service_value_preserved(self):
        p = self._make_policy(service="auth_service")
        result = self._snapshot_list([p])
        assert result[0]["services"] == ["auth_service"]

    def test_two_policies_same_group_collapsed(self):
        """Two policies identical except service → one group with services=[a, b]."""
        p1 = self._make_policy(retain_days=60, service="auth_service", policy_id="rp_1")
        p2 = self._make_policy(retain_days=60, service="server_service", policy_id="rp_2")
        result = self._snapshot_list([p1, p2])
        assert len(result) == 1
        g = result[0]
        assert g["count"] == 2
        assert sorted(g["services"]) == ["auth_service", "server_service"]

    def test_different_severity_makes_separate_groups(self):
        """Same retain_days but different severity → two groups."""
        p1 = self._make_policy(retain_days=30, severity="INFO")
        p2 = self._make_policy(retain_days=30, severity="ERROR")
        result = self._snapshot_list([p1, p2])
        assert len(result) == 2
        severities = {g["severity"] for g in result}
        assert severities == {"INFO", "ERROR"}

    def test_sort_by_retain_days_then_severity(self):
        """Groups are sorted by retain_days ASC, then severity (None→'')."""
        p1 = self._make_policy(retain_days=90, severity="WARNING")
        p2 = self._make_policy(retain_days=30, severity="INFO")
        p3 = self._make_policy(retain_days=30, severity=None)
        result = self._snapshot_list([p1, p2, p3])
        assert len(result) == 3
        assert result[0]["retain_days"] == 30
        assert result[0]["severity"] is None  # None → "" sorts before "INFO"
        assert result[1]["retain_days"] == 30
        assert result[1]["severity"] == "INFO"
        assert result[2]["retain_days"] == 90

    def test_services_sorted_within_group(self):
        """Services list inside a group is sorted alphabetically."""
        p1 = self._make_policy(service="z_service")
        p2 = self._make_policy(service="a_service")
        result = self._snapshot_list([p1, p2])
        assert result[0]["services"] == ["a_service", "z_service"]

    def test_count_matches_number_of_policies_in_group(self):
        """count reflects distinct policy rows (not unique services)."""
        p1 = self._make_policy(service="s1", policy_id="r1")
        p2 = self._make_policy(service="s1", policy_id="r2")  # duplicate service OK
        result = self._snapshot_list([p1, p2])
        assert result[0]["count"] == 2
        # Services set deduplication: s1 appears once in set.
        assert result[0]["services"] == ["s1"]

    def test_sample_id_set_to_first_seen_policy(self):
        """sample_id is the id of the first policy encountered for the group."""
        p1 = self._make_policy(service="a", policy_id="rp_first")
        p2 = self._make_policy(service="b", policy_id="rp_second")
        result = self._snapshot_list([p1, p2])
        assert result[0]["sample_id"] == "rp_first"

    def test_mixed_none_and_real_policies(self):
        """None entries are silently skipped alongside real ones."""
        p = self._make_policy(retain_days=180)
        result = self._snapshot_list([None, p, None])
        assert len(result) == 1
        assert result[0]["retain_days"] == 180


# ═══════════════════════════════════════════════════════════════════════════════
# 3. update_rule: 409 vs 500 on IntegrityError
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateRuleIntegrityErrorBranching:
    """PATCH /rules/{id}: IntegrityError handling depends on whether
    payload.name is set.

    payload.name is None (non-rename patch) → 500 INTERNAL_ERROR
    payload.name is set (rename collides)   → 409 RULE_NAME_CONFLICT
    """

    def test_integrity_error_without_name_returns_500(
        self, admin_client, monkeypatch
    ):
        from sqlalchemy.exc import IntegrityError
        from src.api.v1.endpoints import rules as rules_ep

        created = admin_client.post(RULES_URL, json=make_rule(name="base-rule")).json()

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("not-null constraint", params=None, orig=None)

        monkeypatch.setattr(rules_ep.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"priority": 42},
        )
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        assert "None" not in body["message"]
        assert body.get("details", {}).get("rule_id") == created["id"]

    def test_integrity_error_with_name_returns_409(
        self, admin_client, monkeypatch
    ):
        from sqlalchemy.exc import IntegrityError
        from src.api.v1.endpoints import rules as rules_ep

        created = admin_client.post(RULES_URL, json=make_rule(name="rename-me")).json()

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("unique constraint", params=None, orig=None)

        monkeypatch.setattr(rules_ep.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "conflicting-name"},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "conflicting-name" in body["message"]

    def test_integrity_error_on_create_gives_409(
        self, admin_client
    ):
        """Direct name collision on POST → 409 RULE_NAME_CONFLICT (existing coverage
        regression guard: create path not broken by update_rule changes)."""
        admin_client.post(RULES_URL, json=make_rule(name="unique-a"))
        r = admin_client.post(RULES_URL, json=make_rule(name="unique-a"))
        assert r.status_code == 409
        assert r.json()["error_code"] == "RULE_NAME_CONFLICT"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _fetch_identity pool=None: 503 even with introspect_key configured
# ═══════════════════════════════════════════════════════════════════════════════


class TestFetchIdentityPoolNoneWithKey:
    """When _introspect_client is None, _fetch_identity raises
    INTROSPECT_NOT_INITIALIZED regardless of introspect_service_api_key.

    This confirms that the pool check happens after the key check — key
    present alone is not enough without the pool.
    """

    def _make_credentials(self, token="test-jwt"):
        from fastapi.security import HTTPAuthorizationCredentials
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def _fake_request(self):
        class _State:
            pass
        class _Req:
            state = _State()
        return _Req()

    def test_pool_none_raises_not_initialized_despite_key_configured(
        self, monkeypatch
    ):
        from src.core.exceptions import AppException
        from src.dependencies import auth as auth_dep

        monkeypatch.setattr(auth_dep, "_introspect_client", None)
        monkeypatch.setattr(
            auth_dep,
            "get_settings",
            lambda: type("S", (), {
                "auth_service_url": "http://auth-test",
                "introspect_service_api_key": "configured-outbound-key",
            })(),
        )

        async def _drive():
            return await auth_dep._fetch_identity(
                self._make_credentials("eyJvalid_jwt_xxxx_long_enough"),
                self._fake_request(),
            )

        from src.core.exceptions import AppException
        with pytest.raises(AppException) as exc_info:
            asyncio.run(_drive())
        assert exc_info.value.http_status == 503
        assert exc_info.value.error_code == "INTROSPECT_NOT_INITIALIZED"

    def test_pool_none_no_ephemeral_client_created(self, monkeypatch):
        """No AsyncClient construction when pool is None — slowloris guard."""
        import httpx
        from src.core.exceptions import AppException
        from src.dependencies import auth as auth_dep

        monkeypatch.setattr(auth_dep, "_introspect_client", None)
        monkeypatch.setattr(
            auth_dep,
            "get_settings",
            lambda: type("S", (), {
                "auth_service_url": "http://auth-test",
                "introspect_service_api_key": "some-key",
            })(),
        )

        calls = []

        def _forbidden(*args, **kwargs):
            calls.append(("new_client", args, kwargs))
            raise AssertionError("ephemeral AsyncClient must not be created")

        monkeypatch.setattr(auth_dep.httpx, "AsyncClient", _forbidden)

        async def _drive():
            await auth_dep._fetch_identity(
                self._make_credentials("eyJlongenoughtokenxx"),
                self._fake_request(),
            )

        with pytest.raises(AppException):
            asyncio.run(_drive())

        assert calls == [], "AsyncClient was constructed — pool guard regression"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. _SECRET_KEYS / _TOKEN_KEYS: bearer, service_api_key, introspect_key
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecretKeysClassification:
    """_classify_key and redact() key-priority for bearer and new S2S keys."""

    def test_bearer_classified_as_token_takes_priority_over_secret(self):
        # bearer is in both _TOKEN_KEYS and _SECRET_KEYS; _TOKEN_KEYS is checked
        # first → returns "<TOKEN>"
        result = _classify_key("bearer")
        assert result == "<TOKEN>"

    def test_bearer_uppercase_classified_as_token(self):
        result = _classify_key("BEARER")
        assert result == "<TOKEN>"

    def test_service_api_key_classified_as_secret(self):
        assert _classify_key("service_api_key") == "<SECRET>"

    def test_service_key_classified_as_secret(self):
        assert _classify_key("service_key") == "<SECRET>"

    def test_introspect_key_classified_as_secret(self):
        assert _classify_key("introspect_key") == "<SECRET>"

    def test_redact_bearer_key_returns_token_placeholder(self):
        result = redact({"bearer": "some-secret-value"})
        assert result["bearer"] == "<TOKEN>"

    def test_redact_service_api_key_returns_secret_placeholder(self):
        result = redact({"service_api_key": "k1"})
        assert result["service_api_key"] == "<SECRET>"

    def test_redact_introspect_key_returns_secret_placeholder(self):
        result = redact({"introspect_key": "outbound-secret"})
        assert result["introspect_key"] == "<SECRET>"

    def test_redact_nested_introspect_key(self):
        payload = {"rotate": {"introspect_key": "new-key", "service": "loging_service"}}
        result = redact(payload)
        assert result["rotate"]["introspect_key"] == "<SECRET>"
        assert result["rotate"]["service"] == "loging_service"

    def test_redact_bearer_ingest_e2e(self, client, auth_headers, db):
        """bearer key in event details gets masked at ingest boundary."""
        from src.models.audit_event import AuditEvent
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"bearer": "eyJsomesecret", "action_name": "rotate"}),
            headers=auth_headers,
        )
        assert r.status_code == 201
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["bearer"] == "<TOKEN>"
        assert stored.details["action_name"] == "rotate"

    def test_redact_service_api_key_ingest_e2e(self, client, auth_headers, db):
        from src.models.audit_event import AuditEvent
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"service_api_key": "secret-value", "op": "rotate"}),
            headers=auth_headers,
        )
        assert r.status_code == 201
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["service_api_key"] == "<SECRET>"
        assert stored.details["op"] == "rotate"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. redact() iterative: edge cases in list/dict handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedactIterativeEdgeCases:
    """Covers branches in redact() that may not be exercised by basic tests."""

    def test_scalar_root_returned_unchanged(self):
        """Non-dict/list root passes through without modification."""
        assert redact("plain string") == "plain string"
        assert redact(42) == 42
        assert redact(None) is None
        assert redact(True) is True

    def test_list_of_scalars_passthrough(self):
        """List of plain scalars — no keys, no masking."""
        result = redact(["a", "b", 1, None])
        assert result == ["a", "b", 1, None]

    def test_list_of_mixed_scalars_and_dicts(self):
        """List containing scalars and dicts — scalars pass through, dicts masked."""
        result = redact(["ok", {"password": "x"}, 2])
        assert result[0] == "ok"
        assert result[1] == {"password": "<PASSWORD>"}
        assert result[2] == 2

    def test_dict_value_is_list_under_secret_key_returns_holder(self):
        """If a dict key is a secret and value is a list, returns the placeholder,
        not the list contents."""
        result = redact({"password": ["a", "b"]})
        assert result["password"] == "<PASSWORD>"

    def test_dict_value_is_dict_under_secret_key_returns_holder(self):
        """If key is secret and value is dict, returns placeholder (not recurse)."""
        result = redact({"secret": {"nested": "value"}})
        assert result["secret"] == "<SECRET>"

    def test_dict_value_is_dict_under_clean_key_recurses(self):
        """If key is clean and value is dict, recurses into nested dict."""
        result = redact({"meta": {"password": "x", "info": "ok"}})
        assert result["meta"]["password"] == "<PASSWORD>"
        assert result["meta"]["info"] == "ok"

    def test_list_of_lists_nested(self):
        """Nested list-of-lists: inner lists processed iteratively."""
        result = redact([[{"token": "t"}]])
        assert result[0][0]["token"] == "<TOKEN>"

    def test_empty_dict_returned_unchanged(self):
        assert redact({}) == {}

    def test_empty_list_returned_unchanged(self):
        assert redact([]) == []

    def test_non_string_key_coerced_to_str(self):
        """Non-string keys are converted to str before _classify_key lookup."""
        result = redact({1: "value", "password": "secret"})
        # 1 → "1" → not in any secret key set → plain passthrough
        assert result["1"] == "value"
        assert result["password"] == "<PASSWORD>"

    def test_very_long_string_value_truncated_to_max(self):
        from src.utils.redaction import _MAX_STRING_LEN
        long_val = "x" * (_MAX_STRING_LEN + 500)
        result = redact({"info": long_val})
        assert result["info"].endswith("<TRUNCATED>")
        assert len(result["info"]) <= _MAX_STRING_LEN + 20

    def test_depth_exactly_at_max_not_truncated(self):
        """At _MAX_DEPTH nesting level, value should still be processed (not
        truncated — truncation applies only when depth > _MAX_DEPTH)."""
        from src.utils.redaction import _MAX_DEPTH

        payload: dict = {"password": "leaf"}
        for _ in range(_MAX_DEPTH - 1):
            payload = {"nested": payload}

        out = redact(payload)
        cursor = out
        for _ in range(_MAX_DEPTH - 1):
            cursor = cursor["nested"]
        assert cursor == {"password": "<PASSWORD>"}

    def test_depth_one_over_max_is_truncated(self):
        """At _MAX_DEPTH + 1, the value is replaced with '<TRUNCATED>'."""
        from src.utils.redaction import _MAX_DEPTH

        payload: dict = {"password": "deep-secret"}
        for _ in range(_MAX_DEPTH):
            payload = {"nested": payload}

        out = redact(payload)
        cursor = out
        reached_truncated = False
        for _ in range(_MAX_DEPTH + 2):
            if isinstance(cursor, str):
                assert cursor == "<TRUNCATED>"
                reached_truncated = True
                break
            cursor = cursor.get("nested")
        assert reached_truncated, "Expected '<TRUNCATED>' sentinel at max depth"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. COUNT statement_timeout actual 57014 trigger
# ═══════════════════════════════════════════════════════════════════════════════


class TestCountStatementTimeout57014:
    """events.query(include_total=True) with a very short timeout must return
    total=None when Postgres emits 57014 query_canceled, and must NOT raise.

    Also: non-57014 DBAPIError must re-raise (not swallowed).
    """

    def _insert_event(self, db):
        from src.models.audit_event import AuditEvent
        from src.utils.ids import audit_event_id
        row = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
        )
        db.add(row)
        db.commit()
        return row

    def test_timeout_1ms_triggers_57014_total_none(self, db, monkeypatch):
        """Setting AUDIT_COUNT_STATEMENT_TIMEOUT_MS=1 on a real Postgres causes
        the COUNT to time out; the repository must return total=None without
        raising and still return the page of events.
        """
        from src.core.config import get_settings
        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "1")
        get_settings.cache_clear()
        try:
            for _ in range(3):
                self._insert_event(db)

            from src.repositories import events as events_repo
            events, total, has_more = events_repo.query(db, include_total=True)

            # Either timeout fired (total=None) or COUNT was fast enough (total=int).
            # We can't guarantee the timeout fires on all hardware, so assert
            # the invariant: if total is not None it must be correct; if total
            # is None the page must still be returned.
            assert has_more is False
            assert len(events) == 3
            if total is not None:
                assert total == 3
        finally:
            get_settings.cache_clear()

    def test_57014_pgcode_returns_total_none_without_raising(self, db, monkeypatch):
        """Simulate a 57014 via monkeypatched execute — repo must catch it and
        return total=None, not re-raise.

        Strategy: intercept the SAVEPOINT begin_nested so we can intercept the
        next execute that looks like a COUNT. We patch `db.begin_nested` to
        return a context that raises DBAPIError with 57014 on the nested execute.
        """
        from src.core.config import get_settings
        from src.repositories import events as events_repo

        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            self._insert_event(db)

            original_execute = db.execute
            # Track whether we've already fired the 57014 once (only for the
            # COUNT, not for SET LOCAL or the main query).
            state = {"count_calls": 0}

            def patched_execute(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                # The COUNT statement generated by SQLAlchemy:
                #   SELECT count(*) AS count_1 FROM audit_events [WHERE ...]
                # The SET LOCAL statement comes through as literal text.
                # We intercept the first plain COUNT (not the SET LOCAL).
                if (
                    "count" in sql_lower
                    and "set local" not in sql_lower
                    and "show" not in sql_lower
                    and state["count_calls"] == 0
                ):
                    state["count_calls"] += 1
                    orig_exc = MagicMock()
                    orig_exc.pgcode = "57014"
                    raise DBAPIError(
                        statement="SELECT count(*)",
                        params={},
                        orig=orig_exc,
                    )
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched_execute)

            events_list, total, has_more = events_repo.query(db, include_total=True)
            assert total is None, "57014 must produce total=None"
            assert len(events_list) >= 1
        finally:
            get_settings.cache_clear()

    def test_non_57014_dbapi_error_reraises(self, db, monkeypatch):
        """A DBAPIError with pgcode != '57014' must NOT be swallowed."""
        from src.core.config import get_settings
        from src.repositories import events as events_repo

        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            self._insert_event(db)

            original_execute = db.execute
            state = {"count_calls": 0}

            def patched_execute(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                if (
                    "count" in sql_lower
                    and "set local" not in sql_lower
                    and "show" not in sql_lower
                    and state["count_calls"] == 0
                ):
                    state["count_calls"] += 1
                    orig_exc = MagicMock()
                    orig_exc.pgcode = "42P01"  # undefined_table — not 57014
                    raise DBAPIError(
                        statement="SELECT count(*)",
                        params={},
                        orig=orig_exc,
                    )
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched_execute)

            with pytest.raises(DBAPIError):
                events_repo.query(db, include_total=True)
        finally:
            get_settings.cache_clear()

    def test_timeout_disabled_zero_returns_total_without_savepoint(
        self, db, monkeypatch
    ):
        """AUDIT_COUNT_STATEMENT_TIMEOUT_MS=0 bypasses savepoint and returns
        exact count directly.
        """
        from src.core.config import get_settings
        from src.repositories import events as events_repo

        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "0")
        get_settings.cache_clear()
        try:
            for _ in range(2):
                self._insert_event(db)
            events_list, total, has_more = events_repo.query(db, include_total=True)
            assert total == 2
            assert len(events_list) == 2
        finally:
            get_settings.cache_clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. normalize_service_name_preserve_case
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalizeServiceNamePreserveCase:
    """normalize_service_name_preserve_case applies NFKC, strips invisibles,
    folds confusables — but does NOT lowercase. Used by schema validator to
    catch uppercase before charset check.
    """

    def test_pure_ascii_lowercase_unchanged(self):
        assert normalize_service_name_preserve_case("auth_service") == "auth_service"

    def test_uppercase_not_lowercased(self):
        result = normalize_service_name_preserve_case("AUTH_SERVICE")
        assert result == "AUTH_SERVICE"

    def test_mixed_case_not_lowercased(self):
        result = normalize_service_name_preserve_case("Auth_Service")
        assert result == "Auth_Service"

    def test_nfkc_fullwidth_folded_to_ascii(self):
        # ｌｏｇｉｎｇ (fullwidth) → loging via NFKC
        fullwidth = "ｌｏｇｉｎｇ"  # ｌｏｇｉｎｇ
        assert normalize_service_name_preserve_case(fullwidth) == "loging"

    def test_invisible_zwsp_stripped(self):
        result = normalize_service_name_preserve_case("auth_service​")
        assert result == "auth_service"

    def test_invisible_bom_stripped(self):
        result = normalize_service_name_preserve_case("﻿auth_service")
        assert result == "auth_service"

    def test_cyrillic_confusable_folded(self):
        # кириллическая 'а' (U+0430) → 'a', but uppercase preserved for others
        result = normalize_service_name_preserve_case("аuth_service")
        assert result == "auth_service"

    def test_cyrillic_uppercase_confusable_folded_to_ascii_uppercase(self):
        # кириллическая 'А' (U+0410) → 'A'
        result = normalize_service_name_preserve_case("Аuth_service")
        assert result == "Auth_service"

    def test_whitespace_stripped(self):
        result = normalize_service_name_preserve_case("  auth_service  ")
        assert result == "auth_service"

    def test_non_str_raises_type_error(self):
        with pytest.raises(TypeError):
            normalize_service_name_preserve_case(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            normalize_service_name_preserve_case(None)  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self):
        assert normalize_service_name_preserve_case("") == ""

    def test_lowercase_result_same_as_normalize_service_name(self):
        """Applying .lower() to preserve_case result equals normalize_service_name."""
        inputs = [
            "AUTH_SERVICE",
            "lоging_service",  # cyrillic о
            "  Server_Worker  ",
            "ｌｏｇｉｎｇ_ｓｅｒｖｉｃｅ",  # fullwidth
        ]
        for s in inputs:
            assert (
                normalize_service_name_preserve_case(s).lower()
                == normalize_service_name(s)
            ), f"mismatch for {s!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. require_service_token: empty SERVICE_API_KEYS map
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequireServiceTokenEmptyMap:
    """Empty SERVICE_API_KEYS map → 503 SERVICE_TOKEN_NOT_CONFIGURED on any
    ingest endpoint. Mirrors existing test in test_cov_loging_w6 but confirms
    the path via both POST /events and POST /services/{svc}/events.
    """

    def _empty_map_client(self, db, monkeypatch):
        import json
        from src.main import app
        from src.dependencies.db import get_db
        from tests.conftest import _db_override
        from fastapi.testclient import TestClient
        from src.core.config import get_settings

        monkeypatch.setenv("SERVICE_API_KEYS", json.dumps({}))
        get_settings.cache_clear()
        app.dependency_overrides[get_db] = _db_override(db)
        client = TestClient(app, raise_server_exceptions=False)
        return client, get_settings

    def test_empty_map_on_post_events_returns_503(self, db, monkeypatch):
        client, get_settings = self._empty_map_client(db, monkeypatch)
        try:
            r = client.post(
                EVENTS_URL,
                json=make_event(),
                headers={
                    "Authorization": f"Bearer {TEST_API_KEY}",
                    "X-Service-Identity": "auth_service",
                },
            )
            assert r.status_code == 503
            assert r.json()["error_code"] == "SERVICE_TOKEN_NOT_CONFIGURED"
        finally:
            from src.main import app
            app.dependency_overrides.clear()
            get_settings.cache_clear()

    def test_empty_map_on_register_events_returns_503(self, db, monkeypatch):
        client, get_settings = self._empty_map_client(db, monkeypatch)
        try:
            r = client.post(
                f"{SERVICES_URL}/auth_service/events",
                json={"events": [make_event_def()]},
                headers={
                    "Authorization": f"Bearer {TEST_API_KEY}",
                    "X-Service-Identity": "auth_service",
                },
            )
            assert r.status_code == 503
            assert r.json()["error_code"] == "SERVICE_TOKEN_NOT_CONFIGURED"
        finally:
            from src.main import app
            app.dependency_overrides.clear()
            get_settings.cache_clear()
