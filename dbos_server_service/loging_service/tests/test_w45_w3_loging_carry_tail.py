"""Регрессионные тесты на хвост verified-open carry P3/P4 по loging:

1. `INGEST_BURST_PER_SECOND` — second-tier rule поверх `INGEST_RATE_LIMIT`
   закрывает burst-сценарий (legitimate logon-storm: 10 событий × 200
   юзеров вылетает в `100/minute` за полсекунды). Без burst-капы первая
   секунда выжирает минутный бюджет, дальше — 429 на ~59 секунд.

2. Rule engine cold-start UNLOADED + лежащая БД — `apply_rules`
   пробрасывает исключение наверх через `_cache.get` ↦ `event_service.record`
   ↦ POST /events отвечает 500. Контракт fail-closed: до первого
   удачного refresh'а лучше уронить ingest, чем тайно записать события
   без rule engine (compliance-дыра, SUPPRESS-правила игнорятся).

3. Outbox под pool exhaustion — `_write_batch_locked` зовёт
   `SessionLocal()` синхронно. Если конкурентный workload выжрал
   `pool_size + max_overflow`, второй writer ждёт `pool_timeout`
   секунд и вылетает на `TimeoutError`; row остаются в очереди для
   следующего drain-цикла.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import TimeoutError as SAPoolTimeout

from src.core.config import get_settings
from src.schemas.events import EventCreate
from src.services import event_service, rule_service
from src.services.rule_service import CacheState, _RuleCache

from tests.conftest import make_event


# ── 1. INGEST_BURST_PER_SECOND ────────────────────────────────────────────


class TestIngestBurstCap:
    """Multi-rule `<base>;<N>/second` через `compose_ingest_rate_limit()`."""

    def test_compose_returns_base_when_burst_zero(self, monkeypatch):
        monkeypatch.delenv("INGEST_BURST_PER_SECOND", raising=False)
        monkeypatch.setenv("INGEST_RATE_LIMIT", "100/minute")
        get_settings.cache_clear()
        s = get_settings()
        assert s.ingest_burst_per_second == 0
        assert s.compose_ingest_rate_limit() == "100/minute"

    def test_compose_appends_burst_when_nonzero(self, monkeypatch):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "100/minute")
        monkeypatch.setenv("INGEST_BURST_PER_SECOND", "5")
        get_settings.cache_clear()
        s = get_settings()
        assert s.ingest_burst_per_second == 5
        assert s.compose_ingest_rate_limit() == "100/minute;5/second"

    def test_compose_parses_via_parse_many(self, monkeypatch):
        """`limits.parse_many` должен честно разобрать составную строку.

        Если синтаксис разойдётся с тем, что ждёт slowapi (`;`-сепаратор),
        дефект всплывёт здесь раньше, чем в HTTP-тесте.
        """
        from limits import parse_many

        monkeypatch.setenv("INGEST_RATE_LIMIT", "100/minute")
        monkeypatch.setenv("INGEST_BURST_PER_SECOND", "5")
        get_settings.cache_clear()
        items = parse_many(get_settings().compose_ingest_rate_limit())
        assert len(items) == 2
        # Порядок не гарантирован API, проверяем мультимножество.
        rates = {(it.amount, it.GRANULARITY.seconds) for it in items}
        # 100/minute = (100, 60), 5/second = (5, 1).
        assert rates == {(100, 60), (5, 1)}

    def test_burst_triggers_429_within_minute_budget(
        self, client, auth_headers, monkeypatch
    ):
        """`INGEST_BURST_PER_SECOND=3` режет 4-й запрос в секунду, хотя
        минутный bucket (`60/minute`) ещё не исчерпан.
        """
        monkeypatch.setenv("INGEST_RATE_LIMIT", "60/minute")
        monkeypatch.setenv("INGEST_BURST_PER_SECOND", "3")
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # 3 запроса в одну секунду — все 201.
        for i in range(3):
            r = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert r.status_code == 201, f"#{i} got {r.status_code}: {r.text}"

        # 4-й в ту же секунду — 429, но минутный бюджет (3/60) далеко не выжат.
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert r.status_code == 429
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_default_remains_no_burst(self, monkeypatch):
        """Backward-compat: дефолт `INGEST_BURST_PER_SECOND=0` не добавляет
        второе правило — те же 100/minute, что были до patch'а.
        """
        monkeypatch.delenv("INGEST_BURST_PER_SECOND", raising=False)
        monkeypatch.delenv("INGEST_RATE_LIMIT", raising=False)
        get_settings.cache_clear()
        assert get_settings().compose_ingest_rate_limit() == "100/minute"


# ── 2. Rule engine cold-start UNLOADED + DB down ──────────────────────────


class TestRuleEngineColdStartUnloaded:
    """`_RuleCache.get` при `prev_state == UNLOADED` и упавшем DB-запросе
    обязан пробросить исключение наверх, без stale-fallback'а.

    Условия: `_loaded_monotonic is None` (никогда не загружались), DB
    `get_max_updated_at` бросает — except-ветка идёт в `else: raise`,
    `apply_rules` пропускает исключение к `event_service.record`,
    эндпоинт возвращает 500. Альтернатива (записать без правил) — тихо
    утаила бы события, которые активный SUPPRESS должен был дропнуть.
    """

    def test_cold_start_db_failure_propagates(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=30)
        assert cache._state is CacheState.UNLOADED
        assert cache._loaded_monotonic is None

        from src.repositories import rules as rule_repo

        def _boom(_db):
            raise RuntimeError("db down")

        monkeypatch.setattr(rule_repo, "get_max_updated_at", _boom)

        with pytest.raises(RuntimeError, match="db down"):
            cache.get(db)
        # State откатился к prev_state (UNLOADED) — следующая попытка
        # ингеста снова попробует БД, не стуча в stale.
        assert cache._state is CacheState.UNLOADED
        assert cache._loaded_monotonic is None

    def test_apply_rules_propagates_under_cold_start_db_down(
        self, db, monkeypatch
    ):
        """`apply_rules` не маскирует исключение из `_cache.get` — caller
        получает 500-эквивалент. Self-audit (service=loging_service)
        обходит rule engine и работает даже под DB-down.
        """
        rule_service.invalidate_cache()
        assert rule_service._cache._state is CacheState.UNLOADED

        from src.repositories import rules as rule_repo

        monkeypatch.setattr(
            rule_repo,
            "get_max_updated_at",
            lambda _db: (_ for _ in ()).throw(RuntimeError("db down")),
        )

        payload = EventCreate(**make_event())
        with pytest.raises(RuntimeError, match="db down"):
            rule_service.apply_rules(db, payload)

    def test_event_service_record_500_path_under_cold_start_db_down(
        self, db, monkeypatch
    ):
        """E2E на уровне сервисов: `event_service.record` пробрасывает
        исключение, эндпоинт превратит его в 500 + outbox-retry caller'а.
        """
        rule_service.invalidate_cache()
        from src.repositories import rules as rule_repo

        monkeypatch.setattr(
            rule_repo,
            "get_max_updated_at",
            lambda _db: (_ for _ in ()).throw(RuntimeError("db down")),
        )

        payload = EventCreate(**make_event())
        with pytest.raises(RuntimeError, match="db down"):
            event_service.record(db, payload)

    def test_warm_cache_serves_stale_under_db_down(self, db, monkeypatch):
        """Регрессионная антипара: после первого успешного refresh'а
        DB-failure НЕ пробрасывается — отдаём stale snapshot, бампим
        `stale_serves`. Граница «до vs после первой загрузки».
        """
        cache = _RuleCache(ttl_seconds=0)  # TTL=0 — каждый get идёт в БД
        # Warm-up: один успешный refresh.
        cache.get(db)
        assert cache._loaded_monotonic is not None

        from src.repositories import rules as rule_repo

        monkeypatch.setattr(
            rule_repo,
            "get_max_updated_at",
            lambda _db: (_ for _ in ()).throw(RuntimeError("db down")),
        )

        before = cache._stale_serves
        # Не должно бросить — отдаём stale.
        result = cache.get(db)
        assert isinstance(result, list)
        assert cache._stale_serves == before + 1


# ── 3. Outbox под pool exhaustion ─────────────────────────────────────────


class TestOutboxPoolExhaustion:
    """Контракт: `_write_batch_locked` не глотает `PoolTimeout` из
    `session_factory()`, а пробрасывает в drain-loop, который логирует
    и идёт за следующим батчем. Без этого один выжатый коннект
    заблокировал бы все последующие writers.
    """

    def test_write_batch_locked_surfaces_pool_timeout(self):
        """`_write_batch_locked` сам не глотает TimeoutError из
        `session_factory()` — он выходит наверх (в drain-loop), который
        логирует и продолжает следующий батч. Без этого контракта
        повисший writer заблокировал бы все последующие сессии.
        """
        from src.services.audit_outbox import AuditOutbox, AuditEnvelope

        # Фабрика сессий, которая всегда падает PoolTimeout'ом — моделируем
        # выжатый пул на acquire-стадии.
        def _exhausted_session_factory():
            raise SAPoolTimeout(
                "QueuePool limit of size 1 overflow 0 reached, "
                "connection timed out, timeout 1.00"
            )

        # writer-callable никогда не вызовется — фабрика падает раньше.
        ob = AuditOutbox(
            session_factory=_exhausted_session_factory,
            writer=MagicMock(),
            max_size=16,
            batch_size=1,
            poll_interval_seconds=0.01,
            bump_failure=lambda: 0,
        )

        from datetime import datetime, timezone

        env = AuditEnvelope(
            action="test.action",
            actor_id="usr_test",
            actor_type="user",
            username=None,
            emit_status="success",
            allowed=True,
            request_id=None,
            details={},
            enqueued_at=datetime.now(timezone.utc),
        )

        # `_write_batch_locked` — sync-метод, бросает наверх.
        with pytest.raises(SAPoolTimeout):
            ob._write_batch_locked([env], set())

