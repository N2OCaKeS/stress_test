"""Тесты loging: регрессии по charset/offset/rate-limit/rule cache.

Пункты задачи:
1. idempotency_key charset / offset cap — подтверждение, что фиксы
   на месте (charset pattern, le=MAX_QUERY_OFFSET на /events и /rules).
2. GET /retention rate-limit — подтверждение.
3. RuleCreate.description max_length=1024.
4. _drain_loop cancel-requeue overflow/cancel split — QueueFull при requeue
   считается _dropped_overflow_total, не _dropped_cancel_total.
5. _emit_audit делегирует _emit_audit_envelope (DRY).
6. apply_rules cold-start fail-closed — комментарий-обоснование на месте.
7. _with_statement_timeout сбрасывает timeout перед nested.commit().
8. multiple OVERRIDE_SEVERITY — last-match-wins (см. test_rule_engine_edge).
9. _DEFAULT_SEVERITY fallback покрывает status="warning" → WARNING.
10. _RuleCache._loaded_at/_monotonic atomicity — docstring contract.

Структурные guards идут отдельным классом для каждого пункта; behavioural
тесты используют общую `db`-фикстуру.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading

import pytest
from datetime import datetime, timezone

from src.repositories.events import _with_statement_timeout
from src.schemas.events import EventCreate, _IDEMPOTENCY_KEY_PATTERN
from src.schemas.rules import RuleCreate
from src.services import audit_outbox as ob
from src.services.rule_service import (
    _DEFAULT_SEVERITY,
    _RuleCache,
    _resolve_default_severity,
    apply_rules,
)


# ── 1. idempotency_key charset + offset cap ──────────────────────────────


class TestIdempotencyKeyCharset:
    def test_charset_accepts_uuid_hex(self):
        # 32-char uuid hex — самый частый кейс из audit_outbox.make_envelope
        EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            status="success",
            allowed=True,
            idempotency_key="a" * 32,
        )

    def test_charset_rejects_crlf(self):
        with pytest.raises(ValueError, match="idempotency_key"):
            EventCreate(
                timestamp=datetime.now(timezone.utc),
                service="auth_service",
                action="user.login",
                status="success",
                allowed=True,
                idempotency_key="abc\r\nX-Inject: y",
            )

    def test_pattern_excludes_control_chars(self):
        # Sanity: NUL отбит на pattern-уровне.
        assert _IDEMPOTENCY_KEY_PATTERN.match("abc\x00def") is None
        assert _IDEMPOTENCY_KEY_PATTERN.match("abc.def-1_2") is not None


class TestOffsetCap:
    def _le_from_query(self, query_default):
        """Достаёт `le=...` из FastAPI Query-default через metadata."""
        for m in getattr(query_default, "metadata", []) or []:
            if hasattr(m, "le"):
                return m.le
        return None

    def test_events_offset_cap_in_signature(self):
        from src.api.v1.endpoints.events import list_events
        from src.core.limits import MAX_QUERY_OFFSET
        sig = inspect.signature(list_events)
        offset_param = sig.parameters["offset"]
        assert MAX_QUERY_OFFSET >= 1_000_000
        le = self._le_from_query(offset_param.default)
        assert le == MAX_QUERY_OFFSET, f"offset must have le={MAX_QUERY_OFFSET}, got {le}"

    def test_rules_offset_cap_in_signature(self):
        from src.api.v1.endpoints.rules import list_rules
        from src.core.limits import MAX_QUERY_OFFSET
        sig = inspect.signature(list_rules)
        offset_param = sig.parameters["offset"]
        assert MAX_QUERY_OFFSET >= 1_000_000
        le = self._le_from_query(offset_param.default)
        assert le == MAX_QUERY_OFFSET, f"offset must have le={MAX_QUERY_OFFSET}, got {le}"


# ── 2. GET /retention rate-limit ─────────────────────────────────────────


class TestRetentionGetRateLimit:
    def test_get_policy_has_limiter_decorator(self):
        # `slowapi.Limiter.limit` оборачивает функцию; проверяем, что атрибуты
        # лимитера на месте (`__wrapped__` + `_rate_limit` или подобные маркеры
        # — конкретное имя зависит от версии slowapi). Грубый guard: исходник
        # модуля содержит `@limiter.limit` непосредственно над `def get_policy`.
        import inspect as _ins
        from src.api.v1.endpoints import retention as ret_mod
        source = _ins.getsource(ret_mod)
        # Ищем декоратор сразу над def get_policy.
        match = re.search(
            r"@limiter\.limit\([\s\S]*?\)\s*def get_policy\(",
            source,
        )
        assert match is not None, (
            "GET /retention должен быть под @limiter.limit"
        )


# ── 3. RuleCreate.description max_length ─────────────────────────────────


class TestRuleCreateDescriptionMaxLength:
    def test_description_accepts_under_cap(self):
        r = RuleCreate(name="x", description="a" * 1024, effect="ALLOW")
        assert r.description == "a" * 1024

    def test_description_rejects_over_cap(self):
        with pytest.raises(ValueError):
            RuleCreate(name="x", description="a" * 1025, effect="ALLOW")

    def test_description_optional(self):
        r = RuleCreate(name="x", effect="ALLOW")
        assert r.description is None


# ── 4. _drain_loop cancel-requeue: overflow vs cancel split ──────────────


def _make_outbox(max_size=2, batch_size=2):
    """Build outbox с инжектированными моками — без реальной БД."""
    def session_factory():
        class _StubDB:
            def commit(self): pass
            def rollback(self): pass
            def close(self): pass
            def begin_nested(self): return _SP()
        class _SP:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def commit(self): pass
            def rollback(self): pass
        return _StubDB()

    def writer(db, env):
        pass

    failures = {"n": 0}
    def bump():
        failures["n"] += 1
        return failures["n"]

    return ob.AuditOutbox(
        max_size=max_size,
        batch_size=batch_size,
        poll_interval_seconds=0.001,
        session_factory=session_factory,
        writer=writer,
        bump_failure=bump,
    )


class TestDrainLoopCancelRequeueSplit:
    def test_queuefull_at_requeue_counts_as_overflow(self):
        """При cancel'е с переполнением очереди requeue-loss идёт в overflow."""
        outbox = _make_outbox(max_size=1, batch_size=2)

        async def run():
            outbox.start()
            # Push два envelope'а: после старта drain заберёт сразу батч.
            env_a = ob.make_envelope(
                action="x.a", actor_id=None, actor_type=None,
                username=None, emit_status="success", allowed=True,
                request_id=None, details={},
            )
            env_b = ob.make_envelope(
                action="x.b", actor_id=None, actor_type=None,
                username=None, emit_status="success", allowed=True,
                request_id=None, details={},
            )
            outbox.push_nowait(env_a)
            outbox.push_nowait(env_b)
            await outbox.stop(timeout=2.0)

        asyncio.run(run())
        # При max_size=1 и batch_size=2 второй envelope не помещается в очередь
        # → QueueFull во время requeue → overflow counter +1, cancel counter
        # остаётся 0 (потеря по переполнению, не по cancel'у).
        assert outbox.dropped_overflow_total() == 1
        assert outbox.dropped_cancel_total() == 0

    def test_split_handler_source_structure(self):
        """Структурный guard: ветка `_dropped_overflow_total` живёт в
        `_drain_loop` cancel-handler'е (а не только в `push_nowait`)."""
        source = inspect.getsource(ob.AuditOutbox._drain_loop)
        assert "_dropped_overflow_total" in source, (
            "_drain_loop CancelledError-handler должен инкрементить "
            "overflow при QueueFull во время requeue"
        )
        assert "overflow_lost" in source

    def test_split_doc_source_structure(self):
        """Module-level docstring зеркалит контракт counter'ов."""
        doc = ob.__doc__ or ""
        # Текст после фикса упоминает, что overflow vs cancel разнесены.
        assert "dropped_overflow_total" in doc
        assert "dropped_cancel_total" in doc


# ── 5. _emit_audit делегирует _emit_audit_envelope (DRY) ─────────


class TestEmitAuditDelegates:
    def test_emit_audit_calls_envelope_helper(self, monkeypatch):
        """`_emit_audit` собирает envelope и зовёт `_emit_audit_envelope`."""
        from src import main as main_mod

        captured: list[ob.AuditEnvelope] = []

        def fake_envelope_writer(env):
            captured.append(env)

        monkeypatch.setattr(main_mod, "_emit_audit_envelope", fake_envelope_writer)

        main_mod._emit_audit(
            action="x.test",
            actor_id="usr_1",
            actor_type="user",
            username="alice",
            emit_status="success",
            allowed=True,
            request_id="req_1",
            details={"k": "v"},
        )
        assert len(captured) == 1
        env = captured[0]
        assert env.action == "x.test"
        assert env.actor_id == "usr_1"
        assert env.actor_type == "user"
        assert env.username == "alice"
        assert env.request_id == "req_1"
        assert env.details == {"k": "v"}
        # make_envelope гарантирует UUID-ключ
        assert env.idempotency_key
        assert isinstance(env.idempotency_key, str)


# ── 6. apply_rules cold-start fail-closed комментарий ────────────────────


class TestApplyRulesColdStartFailClosed:
    def test_comment_source_structure(self):
        source = inspect.getsource(apply_rules)
        assert "fail-closed" in source.lower()
        assert "consistency" in source.lower() or "retry" in source.lower()

    def test_event_service_record_doc(self):
        from src.services.event_service import record
        doc = record.__doc__ or ""
        assert "fail-closed" in doc.lower() or "cold-start" in doc.lower()


# ── 7. _with_statement_timeout сбрасывает timeout ─────────────────────────


class TestStatementTimeoutResetsToZero:
    def test_timeout_reset_observed_via_fake_session(self):
        """Фейковая Session ловит SQL-строки; success-путь должен послать
        ровно две `SET LOCAL` — установку и reset."""
        from sqlalchemy import text as sa_text

        executed: list[str] = []

        class _FakeDB:
            def begin_nested(self):
                return _FakeSP(self)

            def execute(self, stmt):
                executed.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
                return None

        class _FakeSP:
            def __init__(self, db): self._db = db
            def commit(self): pass
            def rollback(self): pass

        result = _with_statement_timeout(
            _FakeDB(),
            1500,
            lambda: "ok",
            on_canceled=lambda: "cancelled",
            canceled_log_msg="x",
        )
        assert result == "ok"
        sets = [s for s in executed if "statement_timeout" in s]
        # Один SET LOCAL = 1500, потом SET LOCAL = 0.
        assert len(sets) == 2, f"expected 2 SET LOCAL, got: {sets}"
        assert "1500" in sets[0]
        assert sets[1].strip().endswith("0")


# ── 8. multiple OVERRIDE_SEVERITY ────────────────────────────────────────
# Класс TestOverrideSeverityHighestPriorityWins удалён: контракт
# last-match-wins зафиксирован в test_rule_engine_edge.py::
# test_override_chain_picks_last_match. «Highest priority wins» был
# временной альтернативой и сейчас не реализован.


# ── 9. _DEFAULT_SEVERITY fallback для warning ────────────────────────────


class TestDefaultSeverityWarningFallback:
    def test_warning_status_resolves_to_warning(self):
        # action не в таблице → fallback ветка
        sev = _resolve_default_severity("unknown.action_xyz", "warning")
        assert sev == "WARNING"

    def test_failure_still_warning(self):
        assert _resolve_default_severity("unknown.action_xyz", "failure") == "WARNING"

    def test_denied_still_warning(self):
        assert _resolve_default_severity("unknown.action_xyz", "denied") == "WARNING"

    def test_success_still_info(self):
        assert _resolve_default_severity("unknown.action_xyz", "success") == "INFO"


# ── 10. _RuleCache atomicity docstring contract ──────────────────────────


class TestRuleCacheAtomicityContract:
    def test_init_doc_mentions_lock_contract(self):
        """`__init__` явно описывает пару `_loaded_at`/`_loaded_monotonic`
        и контракт атомарности под `self._lock`."""
        source = inspect.getsource(_RuleCache.__init__)
        assert "атомар" in source.lower() or "lock" in source.lower()

    def test_invalidate_resets_both_loaded_fields(self):
        cache = _RuleCache(ttl_seconds=30)
        cache._loaded_at = datetime.now(timezone.utc)
        cache._loaded_monotonic = 12345.0
        cache._db_empty = True
        cache.invalidate()
        assert cache._loaded_at is None
        assert cache._loaded_monotonic is None
        assert cache._db_empty is False
