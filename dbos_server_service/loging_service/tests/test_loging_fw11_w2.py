"""Точечные unit'ы под четыре фикса в loging_service.

1) `dependencies/auth.py` — на non-200 introspect клиенту уходит константная
   `Authentication service error` без upstream-статуса в теле.
2) `repositories/events.py` — основной SELECT в `query()` тоже под
   `SET LOCAL statement_timeout` (новый config `audit_query_statement_timeout_ms`).
3) `services/audit_outbox.py` — при partial-savepoint failure
   `_drained_total` бампится ровно на число успешных событий, а не на длину
   батча.
4) `api/v1/endpoints/rules.py::create_rule` — IntegrityError с pgcode≠23505
   возвращает 500 INTERNAL_ERROR, а не врёт `RULE_NAME_CONFLICT`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from src.core.config import get_settings
from src.models.audit_event import AuditEvent
from src.repositories import events as events_repo
from src.services.audit_outbox import AuditEnvelope, AuditOutbox
from src.utils.ids import audit_event_id

from tests._helpers import make_env as _env
from tests.conftest import make_rule


RULES_URL = "/api/logging/v1/rules"
EVENTS_URL = "/api/logging/v1/events"


# ── Fix 1: auth.py не светит upstream HTTP-статус в user-facing 503 ─────────


class TestIntrospectStatusNotLeaked:
    def test_non_200_message_is_constant(self, client, mock_introspect, caplog):
        """`Auth service returned 502` больше не уходит наружу.

        Снаружи ловим только константный `Authentication service error`,
        конкретный код 502 видим только в логе.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="src.dependencies.auth"):
            with mock_introspect(status_code=502):
                r = client.get(EVENTS_URL, headers={"Authorization": "Bearer x"})

        assert r.status_code == 503
        body = r.json()
        assert body["error_code"] == "AUTH_SERVICE_ERROR"
        assert body["message"] == "Authentication service error"
        # Никаких 502/upstream-кодов в наружном теле.
        assert "502" not in body["message"]
        assert "returned" not in body["message"].lower()
        # А в логе — есть.
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "502" in log_text


# ── Fix 2: основной SELECT тоже под statement_timeout ──────────────────────


class TestQueryStatementTimeout:
    def test_set_local_applies_to_main_select(self, db, monkeypatch):
        """Перед основным SELECT'ом выставляется конфигурируемый timeout."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "8123")
        get_settings.cache_clear()
        try:
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

            # Снапшотим `statement_timeout` после каждого `SET LOCAL` — для
            # SELECT-гарда он должен совпасть с конфигурируемым значением.
            seen: list[str] = []
            original_execute = db.execute

            def spy(clause, *args, **kwargs):
                result = original_execute(clause, *args, **kwargs)
                sql_text = str(getattr(clause, "text", clause))
                if "set local statement_timeout" in sql_text.lower():
                    seen.append(
                        original_execute(text("SHOW statement_timeout")).scalar_one()
                    )
                return result

            monkeypatch.setattr(db, "execute", spy)

            events, total, has_more = events_repo.query(db, include_total=False)

            assert len(events) == 1
            assert total is None
            assert has_more is False
            # `include_total=False` — был ровно один SET LOCAL, для SELECT'а.
            assert "8123ms" in seen
        finally:
            get_settings.cache_clear()


# ── Fix 3: outbox drained_total бампится только на successful savepoint'ы ──


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class TestOutboxDrainedCounterNotOvercounted:
    def test_partial_failure_drains_only_succeeded(self):
        """В батче из 3 событий: 1 падает, 2 проходят.

        После фикса `drained_total = 2`, `failures = 1`,
        `drained + failures = 3 = enqueued` — инвариант держится.
        Старая семантика: `drained_total = 3` + `failures = 1` ⇒ 4 > 3.
        """
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        def writer(_db, env: AuditEnvelope):
            if env.action == "bad":
                raise RuntimeError("savepoint boom")

        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=8,
                poll_interval_seconds=0.01,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=bump,
            )
            outbox.start()
            try:
                outbox.push_nowait(_env("ok-1"))
                outbox.push_nowait(_env("bad"))
                outbox.push_nowait(_env("ok-2"))
                for _ in range(100):
                    if outbox.drained_total() >= 2 and failures["n"] >= 1:
                        break
                    await asyncio.sleep(0.02)
            finally:
                await outbox.stop(timeout=1.0)
            return outbox.drained_total()

        drained = asyncio.run(run())
        assert drained == 2
        assert failures["n"] == 1
        # Сумма не обгоняет enqueued (3).
        assert drained + failures["n"] == 3


# ── Fix 4: create_rule различает UniqueViolation и прочие IntegrityError ───


class TestCreateRuleIntegrityError:
    def test_non_unique_integrity_error_returns_500_internal(
        self, admin_client, monkeypatch
    ):
        """FK/NOT NULL/CHECK violation на create → 500 INTERNAL_ERROR.

        Раньше любой `IntegrityError` шёл как 409 RULE_NAME_CONFLICT с
        враньём про дубликат имени. После фикса — только pgcode `23505`
        мапится в 409, остальное — 500.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        class _Orig:
            pgcode = "23502"  # NOT NULL violation

        def _boom(db, payload, *, commit=True):
            raise IntegrityError("simulated NOT NULL", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "create", _boom)

        r = admin_client.post(RULES_URL, json=make_rule(name="x"))
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        # Никакого вранья «уже существует».
        assert "already exists" not in body["message"].lower()

    def test_unique_violation_still_returns_409_conflict(
        self, admin_client, monkeypatch
    ):
        """Регрессия: настоящий UNIQUE-конфликт продолжает быть 409."""
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        class _Orig:
            pgcode = "23505"  # UniqueViolation

        def _boom(db, payload, *, commit=True):
            raise IntegrityError("simulated unique", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "create", _boom)

        r = admin_client.post(RULES_URL, json=make_rule(name="dup"))
        assert r.status_code == 409
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "dup" in body["message"]
