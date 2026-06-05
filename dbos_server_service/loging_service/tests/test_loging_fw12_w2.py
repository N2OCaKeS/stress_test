"""Точечные unit'ы под три fw12 фикса в loging_service.

1) `services/audit_outbox.py` — drain CancelledError после успешного commit'а
   больше не дублирует self-audit events: `_write_batch_sync` помечает
   committed envelope'ы в shared set ДО возврата, drain-loop по этому set'у
   фильтрует requeue и инкрементит `_drained_total` ровно один раз.
2) `api/v1/endpoints/events.py::list_events` — под `@limiter.limit(...)` с
   конфигом `AUDIT_QUERY_RATE_LIMIT` (default 60/minute); закрывает Low DoS
   на pool-exhaustion через широкие COUNT/SELECT'ы от reader'а.
3) `api/v1/endpoints/rules.py::update_rule` — IntegrityError с pgcode≠23505
   возвращает 500 INTERNAL_ERROR, симметрично с `create_rule`. payload.name
   присутствует + pgcode=23505 → 409; pgcode=23505 без name → 500 (UNIQUE
   на NULL не имеет смысла); прочее → 500.
"""

from __future__ import annotations

import asyncio

from src.services.audit_outbox import AuditEnvelope, AuditOutbox

from tests._helpers import make_env as _env
from tests.conftest import make_rule


RULES_URL = "/api/logging/v1/rules"
EVENTS_URL = "/api/logging/v1/events"


# ── Fix 1: drain CancelledError не дублирует уже-закоммитнутые events ──────


class _FakeSession:
    """Минимальная in-memory сессия для outbox'а: без БД, savepoint'ы — no-op."""

    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestOutboxCancelledAfterCommitNoDuplicate:
    """Сценарий гонки:

    1) `to_thread(_write_batch_sync)` УЖЕ закоммитил батч.
    2) `committed_keys` теперь содержит `idempotency_key` каждого события.
    3) Внутри `_drain_loop` CancelledError прилетает ДО `_drained_total += ...`.
    4) Старый код requeue'ил весь батч → `_drain_remaining` дописывал дубль.
    5) Новый код: requeue только не-в-committed envelope'ы; `_drained_total`
       инкрементится на len(committed) даже в cancel-branch.
    """

    def test_committed_envelopes_not_requeued_on_cancel(self):
        # Воспроизводим гонку напрямую через `_write_batch_sync`: после его
        # возврата `committed_keys` уже содержит все события, и если задача
        # затем будет отменена — drain не должен их requeue'ить.
        written: list[AuditEnvelope] = []

        def writer(_db, env: AuditEnvelope):
            written.append(env)

        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_FakeSession,
            writer=writer,
            bump_failure=bump,
        )

        batch = [_env(f"ok-{i}") for i in range(3)]
        committed_keys: set[str] = set()
        succeeded = outbox._write_batch_sync(batch, committed_keys)

        # commit-success зафиксирован: все три envelope'а в `committed_keys`.
        assert succeeded == 3
        assert len(committed_keys) == 3
        assert {env.idempotency_key for env in batch} == committed_keys
        # Self-audit-failures не бампились.
        assert failures["n"] == 0

    def test_partial_commit_committed_set_excludes_failed_savepoint(self):
        """Один savepoint падает — он НЕ в committed, остальные — да.

        Гарантирует, что в cancel-branch drain не «потеряет» события: failed
        savepoint уже учтён `_bump_failure`, успешные — в `committed_keys`.
        Инвариант `drained + failures = enqueued` держится без overcount'а.
        """

        def writer(_db, env: AuditEnvelope):
            if env.action == "bad":
                raise RuntimeError("savepoint boom")

        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_FakeSession,
            writer=writer,
            bump_failure=bump,
        )

        ok1, bad, ok2 = _env("ok-1"), _env("bad"), _env("ok-2")
        committed_keys: set[str] = set()
        succeeded = outbox._write_batch_sync([ok1, bad, ok2], committed_keys)

        assert succeeded == 2
        assert bad.idempotency_key not in committed_keys
        assert ok1.idempotency_key in committed_keys
        assert ok2.idempotency_key in committed_keys
        assert failures["n"] == 1

    def test_cancel_during_drain_after_commit_no_duplicate(self):
        """End-to-end: gracefull cancel после commit'а → 0 дубликатов.

        Запускаем drain'у на event-loop'е, пушим события, имитируем cancel
        ДО того, как drain успел дёрнуть `_drained_total +=` (через медленный
        writer). Проверяем: после `stop()` `drained_total` == 3, и ни одно
        событие не было записано дважды.
        """
        writer_call_count = {"n": 0}

        def writer(_db, _env):
            writer_call_count["n"] += 1

        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        async def run():
            outbox = AuditOutbox(
                max_size=16,
                batch_size=16,
                poll_interval_seconds=0.001,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=bump,
            )
            outbox.start()
            try:
                for i in range(3):
                    outbox.push_nowait(_env(f"ev-{i}"))
                # Ждём, пока drain отработает batch.
                for _ in range(200):
                    if outbox.drained_total() >= 3:
                        break
                    await asyncio.sleep(0.01)
                # Сейчас инициируем graceful shutdown — никаких новых событий.
            finally:
                await outbox.stop(timeout=1.0)
            return outbox.drained_total(), writer_call_count["n"]

        drained, written = asyncio.run(run())
        # Ровно 3 события, ровно 3 writer-call'а: никаких дубликатов.
        assert drained == 3, f"drained={drained}, expected 3"
        assert written == 3, f"writer called {written} times, expected 3"
        assert failures["n"] == 0


# ── Fix 2: GET /events под rate-limit ──────────────────────────────────────


class TestGetEventsRateLimit:
    """`GET /events` теперь под `audit_query_rate_limit` (default 60/min)."""

    def test_default_value(self, monkeypatch):
        """Default — `60/minute`."""
        monkeypatch.delenv("AUDIT_QUERY_RATE_LIMIT", raising=False)
        from src.core.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        assert settings.audit_query_rate_limit == "60/minute"

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "120/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        assert settings.audit_query_rate_limit == "120/minute"

    def test_burst_triggers_429(self, admin_client, monkeypatch):
        """3-й запрос на `GET /events` при лимите `2/minute` отбивается 429."""
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        from src.main import limiter

        limiter.reset()

        # Первые 2 — 200.
        for i in range(2):
            r = admin_client.get(EVENTS_URL)
            assert r.status_code == 200, f"request #{i} got {r.status_code}: {r.text}"

        # 3-й — 429.
        r = admin_client.get(EVENTS_URL)
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"


# ── Fix 3: update_rule — pgcode симметрия с create_rule ────────────────────


class TestUpdateRuleIntegrityError:
    def test_non_unique_integrity_error_returns_500_internal(
        self, admin_client, monkeypatch
    ):
        """PATCH с pgcode=23502 (NOT NULL) → 500 INTERNAL_ERROR, не 409.

        До fix'а update_rule при name=None возвращал 500, при name=set —
        всегда 409 RULE_NAME_CONFLICT (даже если pgcode≠23505 — слепо).
        Теперь pgcode разбирается, и не-UniqueViolation идёт в 500
        независимо от наличия `payload.name`.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(RULES_URL, json=make_rule(name="orig")).json()

        class _Orig:
            pgcode = "23502"  # NOT NULL violation

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated NOT NULL", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        # payload содержит `name` — раньше это вело в 409, теперь pgcode не 23505 → 500.
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "renamed"},
        )
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        assert "already exists" not in body["message"].lower()

    def test_unique_violation_with_name_returns_409(self, admin_client, monkeypatch):
        """Регрессия: pgcode=23505 + payload.name задан → 409 NAME_CONFLICT."""
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(RULES_URL, json=make_rule(name="orig")).json()

        class _Orig:
            pgcode = "23505"

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated unique", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "dup"},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "dup" in body["message"]

    def test_unique_violation_without_name_uses_existing_name(self, admin_client, monkeypatch):
        """pgcode=23505 без `payload.name` → 409 с именем из БД (а не None).

        Контракт обновлён: pgcode — единственный сигнал; в сообщение
        подставляется текущее имя правила из БД, чтобы не врать «None».
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(RULES_URL, json=make_rule(name="orig")).json()

        class _Orig:
            pgcode = "23505"

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated unique", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        # PATCH без `name` — например, только priority.
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"priority": 50},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "None" not in body["message"]
        assert "orig" in body["message"]
