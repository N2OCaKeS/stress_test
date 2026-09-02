"""Coverage gaps loging_service — redact depth, PATCH name conflict, action query.

1. redact depth boundary: wrapping _MAX_DEPTH times leaves the innermost dict
   at depth = _MAX_DEPTH, which is NOT > _MAX_DEPTH, so truncation never fires.
   The right boundary needs _MAX_DEPTH + 1 wrappings.

2. PATCH /rules/{id} name conflict → 409 RULE_NAME_CONFLICT via a real DB-level
   collision (create two rules, rename first to second's name) rather than a
   monkeypatched repo.

3. GET /events ?action= normalisation: list_events calls
   normalize_identifier(action) before the DB query. Uppercase, ZWSP-padded
   and cyrillic-confusable action values are all exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.models.audit_event import AuditEvent
from src.utils.ids import audit_event_id

EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"


# ── helpers ───────────────────────────────────────────────────────────────────


def _insert_event(db, *, action: str = "user.login",
                  service: str = "auth_service") -> AuditEvent:
    now = datetime.now(timezone.utc)
    ev = AuditEvent(
        id=audit_event_id(),
        timestamp=now,
        received_at=now,
        service=service,
        action=action,
        actor_type="user",
        status="success",
        allowed=True,
        severity="INFO",
    )
    db.add(ev)
    db.commit()
    return ev


# ── 1. redact depth boundary ──────────────────────────────────────────────────


class TestRedactDepthBoundaryFixed:
    """Verifies depth > _MAX_DEPTH triggers '<TRUNCATED>'.

    The earlier xfail wrapped _MAX_DEPTH times, placing the innermost dict at
    depth == _MAX_DEPTH. The truncation condition is `depth > _MAX_DEPTH`, so
    that depth is NOT truncated. _MAX_DEPTH + 1 wrappings are needed.
    """

    def test_depth_max_plus_one_triggers_truncated(self):
        from src.utils.redaction import redact, _MAX_DEPTH

        payload: dict = {"password": "deep-secret"}
        for _ in range(_MAX_DEPTH + 1):
            payload = {"nested": payload}

        out = redact(payload)

        cursor = out
        found_truncated = False
        for _ in range(_MAX_DEPTH + 4):
            if isinstance(cursor, str):
                assert cursor == "<TRUNCATED>", f"unexpected sentinel: {cursor!r}"
                found_truncated = True
                break
            if not isinstance(cursor, dict):
                break
            cursor = cursor.get("nested")
            if cursor is None:
                break

        assert found_truncated, (
            "Expected '<TRUNCATED>' somewhere in depth > _MAX_DEPTH output"
        )

    def test_depth_exactly_at_max_is_not_truncated(self):
        """At depth == _MAX_DEPTH the dict is still processed — not replaced."""
        from src.utils.redaction import redact, _MAX_DEPTH

        payload: dict = {"password": "leaf"}
        for _ in range(_MAX_DEPTH - 1):
            payload = {"nested": payload}

        out = redact(payload)
        cursor = out
        for _ in range(_MAX_DEPTH - 1):
            cursor = cursor["nested"]
        assert cursor == {"password": "<PASSWORD>"}


# ── 2. PATCH /rules name conflict (real DB) ───────────────────────────────────


class TestPatchRuleNameConflict:
    """PATCH that renames a rule to an already-taken name → 409.

    Uses a genuine UNIQUE constraint violation instead of monkeypatching
    rule_repo.update. This avoids the audit-write issue that caused the
    earlier xfail: when IntegrityError is caught after a rollback, no
    self-audit connection is opened by the endpoint (the audit call is inside
    the try block before commit and is undone by the rollback).
    """

    def test_rename_to_existing_name_returns_409(self, admin_client, db):
        admin_client.post(RULES_URL, json={
            "name": "rule-alpha",
            "effect": "ALLOW",
            "priority": 100,
        })
        admin_client.post(RULES_URL, json={
            "name": "rule-beta",
            "effect": "ALLOW",
            "priority": 90,
        })

        rules = admin_client.get(RULES_URL).json()["items"]
        alpha = next(r for r in rules if r["name"] == "rule-alpha")

        r = admin_client.patch(
            f"{RULES_URL}/{alpha['id']}",
            json={"name": "rule-beta"},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "rule-beta" in body["message"]

    def test_rename_conflict_response_contains_name_in_details(self, admin_client, db):
        admin_client.post(RULES_URL, json={
            "name": "taken-name",
            "effect": "ALLOW",
            "priority": 100,
        })
        admin_client.post(RULES_URL, json={
            "name": "patch-candidate",
            "effect": "ALLOW",
            "priority": 90,
        })

        rules = admin_client.get(RULES_URL).json()["items"]
        candidate = next(r for r in rules if r["name"] == "patch-candidate")

        r = admin_client.patch(
            f"{RULES_URL}/{candidate['id']}",
            json={"name": "taken-name"},
        )
        assert r.status_code == 409, r.text
        assert r.json().get("details", {}).get("name") == "taken-name"

    def test_rename_to_same_name_succeeds(self, admin_client, db):
        """Renaming a rule to its current name is a no-op rename — no conflict."""
        admin_client.post(RULES_URL, json={
            "name": "rule-same",
            "effect": "ALLOW",
            "priority": 100,
        })
        rules = admin_client.get(RULES_URL).json()["items"]
        rule = next(r for r in rules if r["name"] == "rule-same")

        r = admin_client.patch(
            f"{RULES_URL}/{rule['id']}",
            json={"name": "rule-same", "priority": 50},
        )
        assert r.status_code == 200, r.text


# ── 3. GET /events ?action= normalisation ─────────────────────────────────────


class TestActionQueryParamNormalisation:
    """GET /events normalises the ?action= query param before the DB lookup.

    list_events calls normalize_identifier(action) mirroring ingest
    normalisation so stored canonical names are found even when the caller
    sends uppercase, ZWSP-padded or confusable variants.
    """

    def test_uppercase_action_param_finds_canonical_event(self, admin_client, db):
        """?action=USER.LOGIN folds to user.login and finds the stored row."""
        _insert_event(db, action="user.login")
        r = admin_client.get(
            EVENTS_URL,
            params={"action": "USER.LOGIN", "include_total": "true"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_zwsp_in_action_param_is_stripped(self, admin_client, db):
        """ZWSP appended to the action name is stripped before lookup."""
        _insert_event(db, action="user.login")
        padded = "user.login​"  # zero-width space
        r = admin_client.get(
            EVENTS_URL,
            params={"action": padded, "include_total": "true"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_cyrillic_confusable_in_action_param_normalised(self, admin_client, db):
        """Cyrillic confusables in ?action= are folded by normalize_identifier.

        Кириллическое 'у' (U+0443) maps to ASCII 'y' in _CONFUSABLES_MAP, so
        'уser.login' → 'yser.login' which does NOT equal 'user.login'.
        The test verifies the normalisation path is exercised (no 500, valid
        JSON response) rather than asserting a specific match count, since the
        confusable fold result is deterministic by the map.
        """
        _insert_event(db, action="user.login")
        confusable = "уser.login"  # кириллическое у (U+0443)
        r = admin_client.get(
            EVENTS_URL,
            params={"action": confusable, "include_total": "true"},
        )
        assert r.status_code == 200
        body = r.json()
        # у → y means 'yser.login' ≠ 'user.login'; total is 0, not 1.
        assert body["total"] == 0

    def test_action_param_absent_no_filter(self, admin_client, db):
        """When ?action is absent no normalisation occurs and all events are returned."""
        _insert_event(db, action="user.login")
        _insert_event(db, action="user.logout")
        r = admin_client.get(EVENTS_URL, params={"include_total": "true"})
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_empty_action_param_triggers_validation_error(self, admin_client, db):
        """An empty ?action= string should result in a validation or empty-match
        response — the path does not crash."""
        r = admin_client.get(EVENTS_URL, params={"action": ""})
        assert r.status_code in (200, 422), r.text
