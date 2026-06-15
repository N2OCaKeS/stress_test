"""Регрессии под cov-gap (worker).

* `dispatch_outbox.poll_once` — multi-replica на реальном PostgreSQL: два
  concurrent тика делят непересекающиеся подмножества row'ов, ни одна
  не публикуется дважды. До этого `_FakeSession`-тесты в
  `tests/unit/test_dispatch_outbox_poller.py` проверяли SKIP LOCKED
  только формально — реальные lock'и не симулировались.

* `BMC_CIRCUIT_OPEN → run_task lifecycle` — e2e: breaker open у host'а →
  `_breaker.check` в power-handler'е бросает `CircuitBreakerOpenError`
  (наследник `AppException`) → runner кладёт `BMC_CIRCUIT_OPEN` в
  `task.last_error` и в audit-event без падения lifecycle.

`re_attempt_row` уже покрыт в `test_outbox_next_retry_at.py:TestReAttemptRow`
(missing row / already unpublished / DLQ-reset) — дополнительные тесты
не нужны.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal, engine
from src.tasks import dispatch_outbox as outbox_poller
from tests._helpers.broker_mocks import make_broker as _make_broker


pytestmark = pytest.mark.asyncio


# ── dispatch_outbox.poll_once: реальный PG + SKIP LOCKED ────────────────────

_DISPATCH_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS dispatch_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    dispatched_at TIMESTAMPTZ,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dispatch_outbox_pending
    ON dispatch_outbox (created_at)
    WHERE dispatched_at IS NULL;
"""


@pytest.fixture
async def _dispatch_outbox_table(monkeypatch):
    """Создать таблицу `dispatch_outbox` в worker-test БД и навести
    `dispatch_outbox_session.get_session_factory` на тот же engine.

    Прод-конфиг указывает на server_service-БД, но в test-стенде у нас
    один Postgres-контейнер на оба сервиса. Достаточно повесить таблицу
    в текущей БД worker'а и пробросить тот же session-factory.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.execute(text(_DISPATCH_OUTBOX_DDL))
        await conn.execute(text("TRUNCATE dispatch_outbox"))

    monkeypatch.setattr(
        outbox_poller.dispatch_outbox_session,
        "get_session_factory",
        lambda: AsyncSessionLocal,
    )
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS dispatch_outbox CASCADE"))


async def _seed_outbox_rows(n: int, *, task_kind: str = "power.on") -> list[str]:
    task_ids = [f"tsk_{uuid.uuid4().hex[:16]}" for _ in range(n)]
    async with AsyncSessionLocal() as session:
        for tid in task_ids:
            await session.execute(
                text(
                    "INSERT INTO dispatch_outbox (task_id, task_kind, payload) "
                    "VALUES (:tid, :kind, '{}'::jsonb)"
                ),
                {"tid": tid, "kind": task_kind},
            )
        await session.commit()
    return task_ids


async def _count_dispatched() -> int:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM dispatch_outbox "
                    "WHERE dispatched_at IS NOT NULL"
                )
            )
        ).scalar_one()
        return int(row or 0)


class TestDispatchOutboxConcurrentReplicas:
    async def test_two_replicas_split_rows_no_double_kiq(
        self, _dispatch_outbox_table, monkeypatch,
    ):
        """Две конкурентные `poll_once` обрабатывают непересекающиеся
        подмножества: ни одна row не получает двух kiq, суммарно =N."""
        N = 8
        task_ids = await _seed_outbox_rows(N)

        # Замедляем kiq: даём второй «реплике» шанс параллельно стартовать
        # SELECT и зацепить ровно те row'ы, которые первая ещё не успела
        # закоммитить. Без задержки первая может за один проход забрать
        # весь batch (legit, но не тестирует параллелизм).
        kiq_calls: list[str] = []
        kiq_lock = asyncio.Lock()

        async def slow_kiq(task_id, *args, **kwargs):
            await asyncio.sleep(0.005)
            async with kiq_lock:
                kiq_calls.append(task_id)

        from unittest.mock import AsyncMock, MagicMock
        broker = MagicMock()
        kiq = AsyncMock(side_effect=slow_kiq)
        kicker = MagicMock()
        kicker.kiq = kiq
        task = MagicMock()
        task.kicker = MagicMock(return_value=kicker)
        broker.find_task = MagicMock(return_value=task)
        broker._kiq = kiq

        import src.main as _main
        monkeypatch.setattr(_main, "broker", broker)

        # Гарантируем большой batch — обе реплики увидят всю очередь
        # в одном SELECT'е, после чего SKIP LOCKED разведёт row'ы.
        from src.core.config import get_settings
        settings = get_settings()
        original_batch = settings.dispatch_outbox_batch_size
        monkeypatch.setattr(settings, "dispatch_outbox_batch_size", N)

        # Подменяем Redis-redis_pool на noop-fake — SETNX/DEL не блокируют
        # тест, но контракт «dedup_key выставлен → kiq runs» не нарушаем.
        class _NoopRedis:
            async def set(self, *a, **kw):
                return True

            async def delete(self, *a, **kw):
                return 1

        monkeypatch.setattr(
            outbox_poller.redis_pool, "get_redis", lambda: _NoopRedis(),
        )

        results = await asyncio.gather(
            outbox_poller.poll_once(),
            outbox_poller.poll_once(),
        )
        # poll_once возвращает None — главное, что не упало.
        assert results == [None, None]

        # Каждый task_id отправлен ровно один раз.
        assert len(kiq_calls) == N, (
            f"expected {N} kiq calls, got {len(kiq_calls)}"
        )
        assert sorted(kiq_calls) == sorted(task_ids)
        # И ни одного дубликата (главный инвариант SKIP LOCKED).
        assert len(set(kiq_calls)) == N, (
            f"duplicate kiq calls: {[t for t in kiq_calls if kiq_calls.count(t) > 1]}"
        )
        # Все row'и в БД зафиксированы как dispatched.
        assert await _count_dispatched() == N

        # cleanup batch_size override
        monkeypatch.setattr(
            settings, "dispatch_outbox_batch_size", original_batch,
        )


# ── BMC breaker open → run_task lifecycle ────────────────────────────────


class TestBmcCircuitOpenLifecycle:
    """Сквозной тест: открытый breaker → power.on handler ловит
    `CircuitBreakerOpenError`, runner штатно записывает task как FAILED с
    `BMC_CIRCUIT_OPEN` в last_error и в audit-event."""

    async def test_circuit_open_propagates_to_task_and_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from tests.test_task_handlers import (
            _make_task_max1,
            _patch_no_retry,
        )
        from src.services.bmc_circuit_breaker import CircuitBreakerOpenError
        from src.tasks import power

        tid = await _make_task_max1(
            make_task, task_kind="power.on", target_server_id="srv_brk_open",
        )
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "endpoint_url": "https://bmc.brk.test",
                "username": "root",
                "password": "old",
            }

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )

        # breaker open: первый же check бросает CircuitBreakerOpenError.
        check_calls: list[str] = []

        async def _check(host):
            check_calls.append(host)
            raise CircuitBreakerOpenError(host, 30.0)

        # _get_bmc / record_* / aclose не должны вызываться: check фейлит
        # до получения клиента. Подстраховываемся stub'ами.
        bmc_factory_called = {"flag": False}

        async def _bmc_factory(creds, *, prefer="redfish"):
            bmc_factory_called["flag"] = True
            return None  # не должно быть достигнуто

        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr("src.tasks.power._breaker.check", _check)
        monkeypatch.setattr("src.tasks.power._breaker.record_failure", _noop)
        monkeypatch.setattr("src.tasks.power._breaker.record_success", _noop)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.power._aclose_bmc", _noop)

        await power.power_on.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        # error_code AppException попадает в last_error через `str(exc)`.
        assert "BMC_CIRCUIT_OPEN" in (t.last_error or ""), t.last_error
        # breaker отбил до сети — BMC-клиент не создавался.
        assert bmc_factory_called["flag"] is False
        assert check_calls == ["bmc.brk.test"]

        # Audit: status=failure, severity=ERROR (terminal), error содержит
        # стабильный код.
        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "server.power_on"
        assert ev["status"] == "failure"
        assert ev["severity"] == "ERROR"
        assert "BMC_CIRCUIT_OPEN" in ev["details"]["error"]
