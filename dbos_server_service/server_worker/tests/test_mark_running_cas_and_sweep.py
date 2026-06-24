"""Regressions для server_worker.

Покрытие пяти ранее необкатанных веток:

* **mark_running CAS на attempt-guard** — stale Redis-message с
  in-memory `attempt=0` vs обновлённое `attempt` в БД (retry-recovery
  / другая реплика прокрутила attempt). Без attempt-guard'а CAS
  пропускал stale-consumer'а; теперь UPDATE WHERE matches `attempt`,
  возвращает None.

* **commit-fail после mark_running** — registry должна быть очищена
  даже если `session.commit()` упал в `_runner.run_task` сразу после
  `mark_running` (process-local `RUNNING_TASKS` не зависает на ID,
  по которому БД не приняла перехода в running).

* **dispatch_outbox.poll_once: commit-fail после успешного kiq** —
  дедуп-ключ держится, row не редиспатчится, ошибка в WARNING.

* **_flush_outbox_once outer-exception guard** — `locals().get("row_id")`
  работает: row, чьё чтение упало посередине `_publish_one`, попадает
  в `failed_ids`, не блокирует остальной батч.

* **sweep — worker_id выставлен, но heartbeat-row отсутствует** —
  явно отделяем от ветки «heartbeat есть, но stale». Обе должны
  орфаниться, но это два разных path'а в EXISTS-фильтре.

* **breaker-skip cap на next_retry_at** — `retry_after` от breaker'а
  cap'ается `_BREAKER_SKIP_RETRY_CAP_SECONDS`, чтобы row не висела
  весь cooldown breaker'а после его естественного recovery.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox, Task, WorkerHeartbeat
from src.repositories import task as task_repo
from src.repositories import worker_heartbeat as heartbeat_repo
from src.services import audit_outbox_publisher, audit_publisher_breaker
from src.tasks import _runner_state
from src.tasks import dispatch_outbox as outbox_poller
from tests._helpers.broker_mocks import make_broker as _make_broker
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)

pytestmark = pytest.mark.asyncio


# ── mark_running CAS attempt-guard ──────────────────────────────────────────


def _new_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:16]}"


class TestMarkRunningCasAttemptGuard:
    """CAS на mark_running теперь матчит и attempt — отбивает stale-consumer."""

    async def test_stale_in_memory_attempt_does_not_match(self):
        """В БД attempt=2, в in-memory копии attempt=0 → CAS возвращает None.

        Сценарий: B перехватил row и поднял attempt=1, упал; retry-recovery
        вернул row в queued с attempt=1; C проотработал и тоже умер, attempt=2;
        тем временем stale-сообщение для A всё ещё в очереди — A читает row,
        но его in-memory копия отстала на одну итерацию. CAS не должен
        пропустить такого consumer'а.
        """
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_stale",
                "payload": {"server_id": "srv_stale"},
                "status": TaskStatus.QUEUED,
                "attempt": 2,
                "max_attempts": 5,
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        # Снимаем «stale» копию: тот же id, но attempt=0 в памяти.
        async with AsyncSessionLocal() as session:
            stale = await task_repo.get_by_id(session, tid)
            assert stale is not None
            # Имитируем рассинхрон: in-memory копия отстала.
            stale.attempt = 0
            marked = await task_repo.mark_running(
                session, stale, worker_id="stale-replica",
            )
            assert marked is None, (
                "stale-consumer с устаревшим attempt не должен пройти CAS"
            )
            await session.rollback()

        # Row в БД осталась нетронутой — attempt по-прежнему 2, status queued.
        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row.status == TaskStatus.QUEUED
            assert row.attempt == 2
            assert row.worker_id is None
            assert row.started_at is None

    async def test_matching_attempt_passes_cas(self):
        """Совпадение attempt в памяти и БД → CAS проходит, attempt++."""
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_match",
                "payload": {"server_id": "srv_match"},
                "status": TaskStatus.QUEUED,
                "attempt": 1,
                "max_attempts": 5,
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            marked = await task_repo.mark_running(
                session, t, worker_id="winner-replica",
            )
            assert marked is not None
            await session.commit()

        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row.status == TaskStatus.RUNNING
            assert row.attempt == 2
            assert row.worker_id == "winner-replica"


# ── commit-fail после mark_running: RUNNING_TASKS cleanup ───────────────────


class TestRunTaskCommitFailAfterMarkRunning:
    """`session.commit()` в session 1 падает — registry откатывается."""

    async def test_commit_failure_unregisters_running_task(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from src.tasks import _runner

        tid = await make_task(
            task_kind="power.on",
            target_server_id="srv_commit_fail",
        )

        # Стратегия: оборачиваем `task_repo.mark_running` — пропускаем
        # реальный вызов и сразу подменяем `session.commit` той же сессии
        # на одноразовый boom. Дальше код runner'а делает register, потом
        # commit — он и взорвётся. Все остальные commit'ы (на других
        # сессиях) идут как обычно.
        registered_ids: list[str] = []
        original_register = _runner_state.register_running_task
        original_mark_running = task_repo.mark_running

        def fake_register(task_id):
            registered_ids.append(task_id)
            original_register(task_id)

        monkeypatch.setattr(_runner, "register_running_task", fake_register)

        async def patched_mark_running(db, task, **kwargs):
            result = await original_mark_running(db, task, **kwargs)
            # Подменяем commit ровно на этой сессии — следующий вызов взорвётся.
            real_commit = db.commit
            commit_calls = {"n": 0}

            async def boom_commit():
                commit_calls["n"] += 1
                if commit_calls["n"] == 1:
                    raise RuntimeError("simulated commit failure")
                return await real_commit()

            db.commit = boom_commit  # type: ignore[method-assign]
            return result

        monkeypatch.setattr(task_repo, "mark_running", patched_mark_running)
        # `_runner` импортирует task_repo как модуль, патч через task_repo
        # затронет вызов внутри runner'а.

        # Заранее очищаем registry, чтобы тест был детерминированным.
        _runner_state.RUNNING_TASKS.clear()

        impl_called = {"n": 0}

        async def impl(_payload):
            impl_called["n"] += 1
            return {"power_state": "on"}

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await _runner.run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=impl,
            )

        # register был вызван (значит, mark_running прошёл), но commit упал.
        assert tid in registered_ids
        # impl НЕ должен был вызваться — row так и не стал running.
        assert impl_called["n"] == 0
        # Registry откачен — task_id не остался в RUNNING_TASKS.
        assert tid not in _runner_state.RUNNING_TASKS, (
            "RUNNING_TASKS должен быть очищен после commit-fail'а на mark_running"
        )


# ── dispatch_outbox: commit-fail после успешного kiq ────────────────────────


class _Row:
    """In-memory имитация DispatchOutbox-row'и."""

    def __init__(self, *, task_id: str, task_kind: str, attempts: int = 0,
                 priority: int = 0):
        self.id = uuid.uuid4()
        self.task_id = task_id
        self.task_kind = task_kind
        self.payload = {}
        self.attempts = attempts
        self.priority = priority
        self.last_error = None
        self.next_retry_at = None
        self.dispatched_at = None
        self.created_at = datetime.now(timezone.utc)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """Async-session double. `commit_raises_at` — список индексов commit'ов,
    которые должны бросить (1-based: первый commit = 1).
    """

    def __init__(self, rows, *, commit_raises_at: set[int] | None = None):
        self._rows = rows
        self._commit_raises_at = commit_raises_at or set()
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        return _FakeResult(self._rows)

    async def commit(self):
        self.commits += 1
        if self.commits in self._commit_raises_at:
            raise RuntimeError(f"commit #{self.commits} transient fail")

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _install_session(monkeypatch, session):
    monkeypatch.setattr(
        outbox_poller.dispatch_outbox_session,
        "get_session_factory",
        lambda: (lambda: session),
    )


def _install_broker(monkeypatch, broker_mock):
    import src.main as _main
    monkeypatch.setattr(_main, "broker", broker_mock)


class _FakeRedis:
    """Минимальная редис-имитация под SET NX + DELETE."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple] = []
        self.del_calls: list[str] = []

    async def set(self, key, value, *, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def delete(self, key):
        self.del_calls.append(key)
        return self._store.pop(key, None) is not None


class TestDispatchOutboxCommitFailAfterKiq:
    """Commit dispatched_at упал после успешного kiq — dedup-ключ держится,
    row остаётся pending, повторного kiq не будет (next tick попадает в
    dedup-hit ветку).
    """

    async def test_commit_fail_after_kiq_keeps_dedup_key(self, monkeypatch, caplog):
        import logging

        row = _Row(task_id="tsk_commit_kiq", task_kind="power.on")
        # Первый commit (после успешного kiq) — взрываем.
        session = _FakeSession([row], commit_raises_at={1})
        broker = _make_broker()
        redis_fake = _FakeRedis()

        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            outbox_poller.redis_pool, "get_redis", lambda: redis_fake,
        )

        caplog.set_level(logging.WARNING, logger="src.tasks.dispatch_outbox")

        await outbox_poller.poll_once()

        # kiq был вызван ровно один раз.
        assert broker._kiq.call_count == 1
        # dispatched_at не зафиксирован — commit упал, rollback откатил поле.
        # (на _FakeSession rollback — счётчик; реальное поле row было set'нуто
        # в коде ДО commit'а, поэтому проверяем по rollback'у и логам, а не
        # по row.dispatched_at.)
        assert session.rollbacks >= 1
        # Дедуп-ключ остался — DELETE на success-path не дошёл, а unlock на
        # kiq-failure тоже не дошёл (kiq успешен).
        dedup_keys = [k for k, *_ in redis_fake.set_calls]
        assert any("dispatch_dedup" in k for k in dedup_keys)
        assert redis_fake.del_calls == [], (
            "после kiq-success + commit-fail дедуп-ключ должен висеть до TTL"
        )
        # WARNING с правильным сообщением.
        warning_records = [
            r for r in caplog.records
            if r.levelname == "WARNING"
            and "kiq succeeded but commit failed" in r.getMessage()
        ]
        assert len(warning_records) == 1


# ── _flush_outbox_once outer-exception guard (locals.row_id) ────────────────


class TestFlushOutboxOuterExceptionGuard:
    """Outer-exception в `_publish_one` оставляет row в failed_ids на этот
    проход — не блокирует остальной батч, не падает batch-loop.
    """

    async def test_publish_one_exception_isolates_row(self, monkeypatch):
        # Готовим две row'и: первую сломаем через эксплозию в _publish_one,
        # вторая должна нормально опубликоваться.
        async with AsyncSessionLocal() as session:
            broken = AuditOutbox(
                task_id="tsk_broken",
                payload={"action": "server.power_on", "target_id": "srv_broken"},
            )
            healthy = AuditOutbox(
                task_id="tsk_healthy",
                payload={"action": "server.power_on", "target_id": "srv_healthy"},
            )
            session.add_all([broken, healthy])
            await session.commit()
            broken_id = broken.id
            healthy_id = healthy.id

        call_order: list[int] = []
        original_publish = audit_outbox_publisher._publish_one

        async def flaky_publish(s, row):
            call_order.append(row.id)
            if row.id == broken_id:
                # Имитируем баг в `_publish_one` (например, сериализация
                # упала уже после прочтения row.id) — `_flush_outbox_once`
                # должен поймать через outer-guard и записать в failed_ids.
                raise RuntimeError("simulated publish bug")
            return await original_publish(s, row)

        monkeypatch.setattr(
            audit_outbox_publisher, "_publish_one", flaky_publish,
        )

        async def fake_emit(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit", fake_emit,
        )

        published = await audit_outbox_publisher.flush_outbox()
        assert published == 1, (
            "healthy row должна быть опубликована несмотря на сломанную соседку"
        )

        # broken row осталась unpublished, healthy опубликована.
        async with AsyncSessionLocal() as session:
            broken_row = await session.get(AuditOutbox, broken_id)
            healthy_row = await session.get(AuditOutbox, healthy_id)
        assert broken_row.published_at is None
        assert healthy_row.published_at is not None

        # Обе row'и были прочитаны (broken попала в failed_ids только после
        # exception'а).
        assert broken_id in call_order
        assert healthy_id in call_order


# ── sweep: worker_id выставлен, heartbeat-row отсутствует ──────────────────


class TestSweepHeartbeatRowMissing:
    """Различаем две ветки orphan-логики:
      1. worker_id есть, heartbeat-row есть, но last_heartbeat_at stale;
      2. worker_id есть, heartbeat-row вообще отсутствует.
    Обе должны попадать под sweep (EXISTS-фильтр в `list_orphaned_running`
    отдаёт False в обоих случаях), но это разные ветки в SELECT'е.
    """

    async def test_stale_heartbeat_row_is_orphaned(self, fetch_task, monkeypatch):
        """heartbeat-row есть, но last_heartbeat_at старше cutoff'а."""
        from src.main import _settings, tasks_sweep_orphaned

        monkeypatch.setattr(_settings, "worker_orphan_threshold_seconds", 1.0)
        monkeypatch.setattr(_settings, "worker_heartbeat_stale_seconds", 1.0)

        tid = _new_id()
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_stale_hb",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
                "started_at": old_time,
                "worker_id": "replica-stale",
                "created_by": "usr_caller",
                "request_id": "req_test",
            })
            # Heartbeat row существует, но last_heartbeat_at давно.
            await heartbeat_repo.upsert_heartbeat(session, worker_id="replica-stale")
            await session.execute(
                update(WorkerHeartbeat)
                .where(WorkerHeartbeat.worker_id == "replica-stale")
                .values(last_heartbeat_at=old_time)
            )
            await session.commit()

        if hasattr(tasks_sweep_orphaned, "original_func"):
            await tasks_sweep_orphaned.original_func()
        else:
            await tasks_sweep_orphaned()

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "worker_orphaned" in (t.last_error or "")

    async def test_missing_heartbeat_row_is_orphaned(self, fetch_task, monkeypatch):
        """worker_id выставлен, но в worker_heartbeats его нет совсем
        (replica умерла раньше, чем успела первый heartbeat записать).
        """
        from src.main import _settings, tasks_sweep_orphaned

        monkeypatch.setattr(_settings, "worker_orphan_threshold_seconds", 1.0)
        monkeypatch.setattr(_settings, "worker_heartbeat_stale_seconds", 60.0)

        tid = _new_id()
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_missing_hb",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
                "started_at": old_time,
                "worker_id": "replica-never-pinged",
                "created_by": "usr_caller",
                "request_id": "req_test",
            })
            # Heartbeat row НЕ создаём.
            await session.commit()

            # Sanity: убедимся, что в worker_heartbeats действительно нет
            # ничего для этого worker_id.
            hb = (
                await session.execute(
                    select(WorkerHeartbeat).where(
                        WorkerHeartbeat.worker_id == "replica-never-pinged"
                    )
                )
            ).scalar_one_or_none()
            assert hb is None

        if hasattr(tasks_sweep_orphaned, "original_func"):
            await tasks_sweep_orphaned.original_func()
        else:
            await tasks_sweep_orphaned()

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "worker_orphaned" in (t.last_error or "")
        assert "replica-never-pinged" in (t.last_error or "")


# ── breaker-skip cap на next_retry_at ───────────────────────────────────────


class TestBreakerSkipRetryCap:
    """Открытый breaker возвращает большой `retry_after_seconds` (= cooldown).
    Раньше row уходила в backoff на полный cooldown; теперь cap'аем
    `_BREAKER_SKIP_RETRY_CAP_SECONDS`, чтобы row выходила из backoff'а
    быстро — если breaker всё ещё open, `check()` отобьёт её на следующем
    тике без HTTP.
    """

    @pytest.fixture
    def fake_redis(self, monkeypatch):
        return install_fake_redis(monkeypatch, audit_publisher_breaker)

    @pytest.fixture
    def frozen_clock(self, monkeypatch):
        return frozen_clock_fixture(monkeypatch, audit_publisher_breaker)

    async def test_next_retry_at_capped_after_breaker_skip(
        self, fake_redis, frozen_clock, monkeypatch,
    ):
        """Open breaker с большим cooldown'ом → row.next_retry_at в пределах
        cap'а, не cooldown'а.
        """
        # INSERT row.
        async with AsyncSessionLocal() as session:
            row = AuditOutbox(
                task_id="tsk_cap_breaker",
                payload={"action": "server.power_on", "target_id": "srv_cap"},
            )
            session.add(row)
            await session.commit()
            row_id = row.id

        # Доводим breaker до open. retry_after ~= DEFAULT_COOLDOWN_SECONDS.
        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        cooldown = audit_publisher_breaker.DEFAULT_COOLDOWN_SECONDS
        cap = audit_outbox_publisher._BREAKER_SKIP_RETRY_CAP_SECONDS
        assert cooldown > cap, (
            "тест предполагает cooldown >> cap; пересмотри если константы менялись"
        )

        before = datetime.now(timezone.utc)
        published = await audit_outbox_publisher.flush_outbox()
        assert published == 0

        async with AsyncSessionLocal() as session:
            saved = await session.get(AuditOutbox, row_id)
        assert saved.next_retry_at is not None
        delay = (saved.next_retry_at - before).total_seconds()
        # cap = 5s по умолчанию; cooldown по умолчанию 30s. Берём небольшой
        # допуск на разницу clock'ов между breaker'ом и БД.
        assert delay <= cap + 1.0, (
            f"next_retry_at должен быть cap'нут на {cap}s; got delay={delay}"
        )
        assert delay > 0
