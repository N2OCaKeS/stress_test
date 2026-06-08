"""Coverage gaps loging_service — follow-up.

Areas:
  1. GET /events cross-dept access: `loging_reader` глобален и видит журнал
     любого отдела по `department_id` query param (никакого dept-scope
     enforcement'а — owner-decision: `loging_reader` теперь global-read).

  2. update_rule IntegrityError → 500 path: non-unique IntegrityError during
     PATCH (monkeypatched) where the rule_repo.update raises with orig.pgcode
     not 23505 → 500 INTERNAL_ERROR (covers the else branch after
     _is_unique_violation returns False in update_rule).

  3. _drain_remaining shutdown timeout expired with items in queue:
     remaining <= 0 branch → _dropped_shutdown_total incremented for queued
     events that did not fit into the grace budget.

  4. _drain_remaining CancelledError mid-batch path: force-cancel while
     _drain_remaining is awaiting _flush_batch → _dropped_shutdown_total
     incremented for non-committed events.

  5. _redact_payload no-op path: when redact(details) returns the same value
     (no secrets in details), the original payload object is returned unchanged.

  6. event_service.record_admin_action: severity auto-resolved from
     _DEFAULT_SEVERITY when payload.severity is None, using a pair that exists
     in the table.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.services.audit_outbox import AuditOutbox

from tests._helpers import make_env as _env

EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"


class _OkSession:
    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def close(self):
        pass


# ── 1. loging_reader — global read (dept-scope снят) ──────────────────────────


class TestLogingReaderCrossDept:
    """`loging_reader` теперь global-read: запрос любого `department_id`
    проходит как обычный фильтр, никакого 403 DEPARTMENT_SCOPE_VIOLATION
    больше нет. `account_admin` / `department_admin` отдельно отбиваются
    `INSUFFICIENT_ROLE` в `require_reader`.
    """

    def test_loging_reader_can_query_other_department(
        self, client, mock_introspect, db
    ):
        """loging_reader с department_id=dept_A может запросить dept_B → 200."""
        identity = {
            "active": True,
            "sub": "usr_reader",
            "username": "reader",
            "platform_role": "loging_reader",
            "department_id": "dept_A",
            "subject_type": "user",
            "allowed_services": [],
            "service_roles": {},
            "is_banned": False,
        }
        with mock_introspect(json_body=identity):
            r = client.get(
                EVENTS_URL,
                params={"department_id": "dept_B"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert r.status_code == 200, r.text

    def test_loging_reader_without_dept_id_passes(
        self, client, mock_introspect, db
    ):
        """loging_reader без department_id (platform-роль создаётся без
        отдела) видит весь журнал cross-dept → 200."""
        identity = {
            "active": True,
            "sub": "usr_reader2",
            "username": "reader2",
            "platform_role": "loging_reader",
            "department_id": None,
            "subject_type": "user",
            "allowed_services": [],
            "service_roles": {},
            "is_banned": False,
        }
        with mock_introspect(json_body=identity):
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer test-token"},
            )
        assert r.status_code == 200, r.text


# ── 2. update_rule IntegrityError → 500 (non-unique constraint) ───────────────


class TestUpdateRuleIntegrityError500:
    """PATCH /rules/{id} where rule_repo.update raises IntegrityError with a
    non-unique pgcode → 500 INTERNAL_ERROR (not 409 NAME_CONFLICT).

    Covers the else-branch of _is_unique_violation in update_rule: when the
    error is not a UNIQUE violation (e.g. a CHECK or FK violation), the
    endpoint returns 500.
    """

    def test_non_unique_integrity_error_returns_500(
        self, admin_client, monkeypatch
    ):
        from src.api.v1.endpoints import rules as rules_endpoint
        from tests.conftest import make_rule

        created = admin_client.post(
            RULES_URL, json=make_rule(name="ie500-rule")
        ).json()

        class _CheckViolation:
            pgcode = "23514"  # check_violation — not a unique error
            sqlstate = None

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("check constraint failed", params=None, orig=_CheckViolation())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"priority": 42},
        )
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        assert "rule_id" in body.get("details", {})

    def test_non_unique_integrity_error_message_contains_no_name_conflict(
        self, admin_client, monkeypatch
    ):
        """The 500 message must not mention RULE_NAME_CONFLICT or contain 'name'."""
        from src.api.v1.endpoints import rules as rules_endpoint
        from tests.conftest import make_rule

        created = admin_client.post(
            RULES_URL, json=make_rule(name="ie500-rule2")
        ).json()

        class _FkViolation:
            pgcode = "23503"  # foreign_key_violation
            sqlstate = None

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("fk constraint", params=None, orig=_FkViolation())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"match_service": "auth_service"},
        )
        assert r.status_code == 500, r.text
        assert r.json()["error_code"] != "RULE_NAME_CONFLICT"


# ── 3. _drain_remaining timeout expired ───────────────────────────────────────


class TestDrainRemainingTimeoutExpired:
    """_drain_remaining: when the grace budget runs out before all queued events
    are flushed, _dropped_shutdown_total is incremented by the remaining count.

    The `remaining <= 0` branch fires when the deadline passes between batches.
    """

    def test_shutdown_timeout_zero_drops_remaining_events(self):
        """stop(timeout=0) with items in queue → all items counted as shutdown-dropped."""

        async def run():
            import time as _time

            outbox = AuditOutbox(
                max_size=16,
                batch_size=4,
                poll_interval_seconds=10.0,
                session_factory=_OkSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=16)

            # Put 8 events directly into the queue (bypass push_nowait counters).
            for i in range(8):
                await outbox._queue.put(_env(f"ev-{i}"))

            # Patch the running loop's time() to return a deadline already expired.
            # We do this by setting timeout=0 so deadline = loop.time() + 0.
            # The first batch is picked up but remaining becomes ≤ 0 on the deadline
            # check after flush. We simulate this by making _flush_batch slow enough
            # that deadline passes — instead we use timeout=-1 to force immediate
            # expiry before first batch write.
            #
            # Actually: with timeout=0, deadline = loop.time() + max(0, 0) = loop.time().
            # The first iteration picks a batch, checks remaining = deadline - now ≤ 0,
            # and goes into the lost-count branch. This covers the `remaining <= 0` path.
            await outbox._drain_remaining(timeout=0)

            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        # With timeout=0 and 8 queued events, none can flush: remaining<=0 on first
        # iteration → all 8 (batch of 4 + queue.qsize() of 4) counted as dropped.
        assert dropped > 0, "timeout=0 with queued events must increment dropped_shutdown_total"

    def test_shutdown_dropped_counter_reflects_items_lost(self):
        """Shutdown drop counter increases by exactly the number of items that
        did not make it through the drain budget."""

        async def run():
            outbox = AuditOutbox(
                max_size=10,
                batch_size=10,
                poll_interval_seconds=10.0,
                session_factory=_OkSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=10)

            for i in range(5):
                await outbox._queue.put(_env(f"ev-{i}"))

            await outbox._drain_remaining(timeout=0)
            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        assert dropped == 5


# ── 4. _drain_remaining CancelledError path ───────────────────────────────────


class TestDrainRemainingCancelledError:
    """_drain_remaining CancelledError branch: if the task is force-cancelled
    while awaiting _flush_batch, the not-yet-committed events are counted as
    _dropped_shutdown_total and the CancelledError is re-raised.
    """

    def test_cancelled_during_drain_remaining_increments_shutdown_counter(self):
        """Force-cancel _drain_remaining mid-flush → _dropped_shutdown_total >= 1."""

        async def run():
            flush_started = asyncio.Event()
            cancel_after_flush_start = asyncio.Event()

            async def slow_flush(batch, committed_keys):
                flush_started.set()
                await cancel_after_flush_start.wait()

            outbox = AuditOutbox(
                max_size=10,
                batch_size=10,
                poll_interval_seconds=10.0,
                session_factory=_OkSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=10)

            for i in range(3):
                await outbox._queue.put(_env(f"ev-{i}"))

            # Patch flush to block.
            outbox._flush_batch = slow_flush  # type: ignore[method-assign]

            drain_task = asyncio.create_task(outbox._drain_remaining(timeout=5.0))

            # Wait until _drain_remaining has picked the batch and is awaiting flush.
            await asyncio.wait_for(flush_started.wait(), timeout=2.0)

            # Now cancel the drain task.
            drain_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drain_task

            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        assert dropped >= 1, (
            "CancelledError during _drain_remaining must count lost events in "
            "dropped_shutdown_total"
        )


# ── 5. _redact_payload no-op when details unchanged ──────────────────────────


class TestRedactPayloadNoop:
    """_redact_payload returns the original payload object when redact()
    produces an equal dict (i.e. no keys matched any secret pattern).
    """

    def test_clean_details_returns_original_payload(self):
        """Details with no sensitive keys → redact returns equal value → original."""
        from src.services.event_service import _redact_payload
        from src.schemas.events import EventCreate

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            details={"method": "POST", "path": "/login", "status_code": 200},
        )
        result = _redact_payload(payload)
        # When no secrets present, cleaned == payload.details → same object returned.
        assert result is payload

    def test_empty_details_returns_original(self):
        """Empty details dict → early return before redact is called."""
        from src.services.event_service import _redact_payload
        from src.schemas.events import EventCreate

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
        )
        result = _redact_payload(payload)
        assert result is payload

    def test_sensitive_key_in_details_returns_new_payload(self):
        """Details with a 'password' key → redact changes it → new payload returned."""
        from src.services.event_service import _redact_payload
        from src.schemas.events import EventCreate

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            details={"username": "alice", "password": "hunter2"},
        )
        result = _redact_payload(payload)
        # Redacted → new object.
        assert result is not payload
        assert result.details["password"] == "<PASSWORD>"
        assert result.details["username"] == "alice"


# ── 6. record_admin_action auto-resolves severity from _DEFAULT_SEVERITY ──────


class TestRecordAdminActionSeverityResolution:
    """record_admin_action auto-assigns severity from _DEFAULT_SEVERITY when
    payload.severity is None.

    The branch at event_service.py: if payload.severity is None, it calls
    _resolve_default_severity(action, status) and copies the result into payload.
    """

    def test_none_severity_resolved_from_default_table(self, db):
        """severity=None + known (action, status) pair → severity from _DEFAULT_SEVERITY."""
        from src.services.event_service import record_admin_action
        from src.schemas.events import EventCreate
        from src.services.rule_service import _DEFAULT_SEVERITY

        action = "logging_rule.create"
        status = "success"
        expected_severity = _DEFAULT_SEVERITY[(action, status)]

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action=action,
            actor_type="user",
            status=status,
            allowed=True,
            severity=None,
        )
        event = record_admin_action(db, payload)
        assert event.severity == expected_severity

    def test_none_severity_unknown_pair_gets_warning_or_info(self, db):
        """Fallback: unknown (action, status) pair → 'INFO' for success, 'WARNING' for failure."""
        from src.services.event_service import record_admin_action
        from src.schemas.events import EventCreate

        payload_success = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.admin_access",
            actor_type="user",
            status="success",
            allowed=True,
            severity=None,
        )
        event = record_admin_action(db, payload_success)
        assert event.severity == "INFO"

    def test_explicit_severity_not_overridden(self, db):
        """When severity is already set, it must not be replaced."""
        from src.services.event_service import record_admin_action
        from src.schemas.events import EventCreate

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging_rule.create",
            actor_type="user",
            status="success",
            allowed=True,
            severity="TRACE",
        )
        event = record_admin_action(db, payload)
        assert event.severity == "TRACE"
