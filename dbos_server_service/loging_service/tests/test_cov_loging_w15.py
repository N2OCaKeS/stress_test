"""Покрытие gaps loging_service после F-W14.

1. rules.get_all / get_by_id — фактическое срабатывание 57014 (query_canceled):
   COUNT → 0, SELECT → [], get_by_id → None без raise наружу.
   non-57014 DBAPIError пробрасывается.

2. rules.get_by_id при timeout=0 — обёртка не вызывается (нет SET LOCAL).

3. _RuleCache: переход _db_empty=True → False (БД была пустой, появилось правило).
   Следующий TTL-tick обязан подтянуть новые правила.

4. dropped_cancel_total счётчик: отмена flush во время drain без committed батча
   инкрементит _dropped_cancel_total ровно на число не-committed событий.

5. audit_access middleware: 429 на POST /services/{svc}/events аудитируется как
   http.client_error со status_code=429 (симметрично POST /events).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from src.core.config import get_settings
from src.models.audit_rule import AuditRule
from src.repositories import rules as rules_repo
from src.services.audit_outbox import AuditEnvelope, AuditOutbox, make_envelope
from src.services.rule_service import _RuleCache
from src.utils.ids import audit_rule_id
from tests.conftest import make_event_def

SERVICES_URL = "/api/logging/v1/services"


# ── helpers ──────────────────────────────────────────────────────────────────


def _insert_rule(db, name: str = "rule-w15") -> AuditRule:
    now = datetime.now(timezone.utc)
    rule = AuditRule(
        id=audit_rule_id(),
        name=name,
        description="",
        is_active=True,
        priority=100,
        effect="ALLOW",
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    db.commit()
    return rule


def _env(action: str = "user.login") -> AuditEnvelope:
    return make_envelope(
        action=action,
        actor_id=None,
        actor_type=None,
        username=None,
        emit_status="success",
        allowed=True,
        request_id=None,
        details={},
    )


class _FakeSession:
    def __init__(self):
        self.commits = 0
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
        pass

    def close(self):
        self.closes += 1


# ── 1. rules.get_all — 57014 срабатывает ─────────────────────────────────────


class TestRulesGetAll57014:
    """get_all: COUNT и SELECT под statement_timeout.
    При pgcode 57014: COUNT → 0, SELECT → [].
    При non-57014: re-raise.
    """

    def test_count_57014_returns_zero_total(self, db, monkeypatch):
        """57014 на COUNT → total=0, SELECT продолжает нормально."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            _insert_rule(db, name="rule-57014-count")

            original_execute = db.execute
            state = {"count_calls": 0}

            def patched(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                if (
                    "count" in sql_lower
                    and "audit_rules" in sql_lower
                    and "set local" not in sql_lower
                    and state["count_calls"] == 0
                ):
                    state["count_calls"] += 1
                    orig_exc = MagicMock()
                    orig_exc.pgcode = "57014"
                    orig_exc.sqlstate = None
                    raise DBAPIError("COUNT timeout", {}, orig_exc)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched)

            rules, total = rules_repo.get_all(db)
            assert total == 0, "57014 на COUNT должен вернуть total=0"
        finally:
            get_settings.cache_clear()

    def test_select_57014_returns_empty_page(self, db, monkeypatch):
        """57014 на SELECT → rules=[], total не None (COUNT прошёл)."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            _insert_rule(db, name="rule-57014-select")

            original_execute = db.execute
            state = {"select_calls": 0}

            def patched(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                if (
                    "from audit_rules" in sql_lower
                    and "order by" in sql_lower
                    and "set local" not in sql_lower
                    and state["select_calls"] == 0
                ):
                    state["select_calls"] += 1
                    orig_exc = MagicMock()
                    orig_exc.pgcode = "57014"
                    orig_exc.sqlstate = None
                    raise DBAPIError("SELECT timeout", {}, orig_exc)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched)

            rules, total = rules_repo.get_all(db)
            assert rules == [], "57014 на SELECT должен вернуть пустую страницу"
        finally:
            get_settings.cache_clear()

    def test_non_57014_dbapierror_reraises(self, db, monkeypatch):
        """Прочая DBAPIError (42P01 и т.д.) не подавляется."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            _insert_rule(db, name="rule-non57014")

            original_execute = db.execute
            state = {"select_calls": 0}

            def patched(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                if (
                    "from audit_rules" in sql_lower
                    and "order by" in sql_lower
                    and "set local" not in sql_lower
                    and state["select_calls"] == 0
                ):
                    state["select_calls"] += 1
                    orig_exc = MagicMock()
                    orig_exc.pgcode = "42P01"
                    orig_exc.sqlstate = None
                    raise DBAPIError("undefined table", {}, orig_exc)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched)

            with pytest.raises(DBAPIError):
                rules_repo.get_all(db)
        finally:
            get_settings.cache_clear()


# ── 2. rules.get_by_id — 57014 и timeout=0 ───────────────────────────────────


class TestRulesGetById57014:
    """get_by_id: 57014 → None; non-57014 → re-raise; timeout=0 → нет SET LOCAL."""

    def test_57014_returns_none(self, db, monkeypatch):
        """57014 на db.get(AuditRule, rule_id) → None без raise."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            rule = _insert_rule(db, name="rule-getbyid-57014")

            original_get = db.get
            state = {"calls": 0}

            def patched_get(model, pk):
                state["calls"] += 1
                orig_exc = MagicMock()
                orig_exc.pgcode = "57014"
                orig_exc.sqlstate = None
                raise DBAPIError("get timeout", {}, orig_exc)

            monkeypatch.setattr(db, "get", patched_get)

            result = rules_repo.get_by_id(db, rule.id)
            assert result is None, "57014 на get_by_id должен вернуть None"
        finally:
            get_settings.cache_clear()

    def test_non_57014_reraises(self, db, monkeypatch):
        """Non-57014 DBAPIError в get_by_id пробрасывается наружу."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            rule = _insert_rule(db, name="rule-getbyid-non57014")

            def patched_get(model, pk):
                orig_exc = MagicMock()
                orig_exc.pgcode = "23505"
                orig_exc.sqlstate = None
                raise DBAPIError("some db error", {}, orig_exc)

            monkeypatch.setattr(db, "get", patched_get)

            with pytest.raises(DBAPIError):
                rules_repo.get_by_id(db, rule.id)
        finally:
            get_settings.cache_clear()

    def test_timeout_zero_skips_set_local(self, db, monkeypatch):
        """timeout_ms=0 → get_by_id обходит _with_statement_timeout, нет SET LOCAL."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "0")
        get_settings.cache_clear()
        try:
            rule = _insert_rule(db, name="rule-getbyid-zero")

            captured: list[str] = []
            original_execute = db.execute

            def spy(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                if "set local statement_timeout" in sql_text.lower():
                    captured.append(sql_text)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", spy)

            found = rules_repo.get_by_id(db, rule.id)
            assert found is not None
            assert found.id == rule.id
            assert captured == [], "timeout=0 не должен вызывать SET LOCAL"
        finally:
            get_settings.cache_clear()


# ── 3. _RuleCache: переход empty→non-empty ────────────────────────────────────


class TestRuleCacheEmptyToNonEmpty:
    """`_db_empty=True` → БД получила первое правило → следующий TTL-tick обязан
    подтянуть его даже если `_loaded_at > max(updated_at)` по значению
    (возникнуть не может при нормальном потоке, но ветка `db_empty_now and not
    self._db_empty` должна триггерить get_active_sorted при переходе).
    """

    def test_empty_db_then_rule_added_reloads_on_next_tick(self, db):
        cache = _RuleCache(ttl_seconds=0)
        # Первая загрузка: пустая БД → _db_empty=True.
        rules = cache.get(db)
        assert rules == []
        assert cache._db_empty is True

        # Добавляем правило.
        time.sleep(0.01)
        _insert_rule(db, name="first-rule")
        time.sleep(0.01)

        # Следующий tick после TTL: MAX(updated_at) стал non-NULL →
        # `changed=True` → get_active_sorted должен вернуть правило.
        rules = cache.get(db)
        assert len(rules) == 1
        assert rules[0].name == "first-rule"
        assert cache._db_empty is False

    def test_nonempty_db_becomes_empty_after_delete(self, db):
        """Все правила удалены (soft-delete) → MAX(updated_at) по-прежнему не NULL
        (soft-delete бампит updated_at), поэтому `changed=True` и кеш обновляется
        до пустого списка.
        """
        cache = _RuleCache(ttl_seconds=0)
        rule = _insert_rule(db, name="del-becomes-empty")
        time.sleep(0.01)
        rules = cache.get(db)
        assert len(rules) == 1

        time.sleep(0.01)
        rules_repo.delete(db, rule)
        time.sleep(0.01)

        rules = cache.get(db)
        assert rules == [], "после удаления кеш должен вернуть пустой список"
        # _db_empty=False: MAX(updated_at) не NULL (deleted row ещё в таблице).
        assert cache._db_empty is False


# ── 4. dropped_cancel_total ────────────────────────────────────────────────────


class TestDroppedCancelTotal:
    """`_dropped_cancel_total` инкрементится ровно на число non-committed
    событий при отмене drain-task'а во время flush.
    """

    def test_cancel_during_flush_increments_cancel_counter(self):
        """Симулируем: flush начат (батч взят из очереди), но до commit'а
        прилетает CancelledError. Очередь переполнена — requeue не проходит.
        Все события идут в _dropped_cancel_total.
        """
        async def run():
            outbox = AuditOutbox(
                max_size=2,
                batch_size=4,
                poll_interval_seconds=10.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=2)

            batch = [_env(f"ev-{i}") for i in range(3)]
            committed_ids: set[int] = set()

            # Подменяем sync-часть: ничего не коммитит (committed_ids пустой).
            def fake_write_batch_sync(b, cids):
                return 0

            outbox._write_batch_sync = fake_write_batch_sync  # type: ignore[assignment]

            # Имитируем cancel-ветку из _drain_loop: батч не закоммичен,
            # очередь переполнена (maxsize=2, batch из 3 → все не requeue'ятся).
            requeued = 0
            for envelope in batch:
                if id(envelope) in committed_ids:
                    continue
                try:
                    outbox._queue.put_nowait(envelope)
                    requeued += 1
                except asyncio.QueueFull:
                    break
            lost = len(batch) - len(committed_ids) - requeued
            if lost > 0:
                with outbox._counters_lock:
                    outbox._dropped_cancel_total += lost

            return outbox.dropped_cancel_total()

        cancel_dropped = asyncio.run(run())
        # Очередь вмещает maxsize=2; 1 requeue'ился, 2 потерялись.
        assert cancel_dropped == 1

    def test_cancel_after_full_commit_zero_cancel_dropped(self):
        """Если весь батч закоммичен перед cancel — в _dropped_cancel_total 0."""
        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=4,
                poll_interval_seconds=10.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=8)

            batch = [_env(f"ev-{i}") for i in range(3)]
            committed_ids: set[int] = {id(e) for e in batch}

            # Все в committed_ids → requeue'ить нечего, lost=0.
            requeued = 0
            for envelope in batch:
                if id(envelope) in committed_ids:
                    continue
                try:
                    outbox._queue.put_nowait(envelope)
                    requeued += 1
                except asyncio.QueueFull:
                    break
            lost = len(batch) - len(committed_ids) - requeued
            if lost > 0:
                with outbox._counters_lock:
                    outbox._dropped_cancel_total += lost
            if committed_ids:
                with outbox._counters_lock:
                    outbox._drained_total += len(committed_ids)

            return outbox.dropped_cancel_total(), outbox.drained_total()

        cancel_dropped, drained = asyncio.run(run())
        assert cancel_dropped == 0
        assert drained == 3

    def test_dropped_total_aggregates_all_three_counters(self):
        """dropped_total() = overflow + cancel + shutdown."""
        async def run():
            outbox = AuditOutbox(
                max_size=1,
                batch_size=1,
                poll_interval_seconds=10.0,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=1)

            with outbox._counters_lock:
                outbox._dropped_overflow_total = 2
                outbox._dropped_cancel_total = 3
                outbox._dropped_shutdown_total = 5

            return outbox.dropped_total()

        total = asyncio.run(run())
        assert total == 10


# ── 5. audit_access: 429 на POST /services/{svc}/events ──────────────────────


def _wait_for_event(db, action: str, status_code: int, timeout: float = 1.5):
    """Polling: ждём пока drain outbox'а положит событие в audit_events."""
    from sqlalchemy import select
    from src.models.audit_event import AuditEvent

    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.action == action)
        ).scalars().all()
        for r in rows:
            details = r.details or {}
            if details.get("status_code") == status_code:
                return r
        time.sleep(0.05)
    return None


def test_429_on_register_events_is_audited(
    client, auth_headers, db, TestSessionLocal, monkeypatch
):
    """3-й POST /services/auth_service/events при REGISTER_EVENTS_RATE_LIMIT=2/minute
    отбивается 429 и пишется в audit как http.client_error со status_code=429.

    Закрывает тот же паттерн, что test_audit_middleware_ingest_429.py, но для
    регистрационного пути. _INGEST_PREFIXES включает `/api/logging/v1/services/`,
    поэтому 429 должен проходить через ветку аудита наравне с POST /events.
    """
    import src.db.session as session_module
    monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)

    monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "2/minute")
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.main import limiter
    limiter.reset()

    svc_url = f"{SERVICES_URL}/auth_service/events"
    payload = {"events": [make_event_def(action="user.login")]}
    headers = {**auth_headers, "X-Service-Identity": "auth_service"}

    for i in range(2):
        r = client.post(svc_url, json=payload, headers=headers)
        assert r.status_code == 200, f"request #{i} got {r.status_code}: {r.text}"

    r = client.post(svc_url, json=payload, headers=headers)
    assert r.status_code == 429, r.text

    audited = _wait_for_event(db, "http.client_error", 429)
    assert audited is not None, (
        "429 на POST /services/{svc}/events должен попасть в audit-журнал"
    )
    assert audited.details.get("status_code") == 429
    assert "/services/" in audited.details.get("path", "")
    assert audited.details.get("method") == "POST"
