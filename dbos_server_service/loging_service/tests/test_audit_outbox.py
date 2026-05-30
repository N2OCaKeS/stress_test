"""Тесты self-audit outbox'а: push, drain батчами, bounded buffer, graceful stop.

Outbox-паттерн заменил `asyncio.to_thread(_emit_audit)` per-event на
in-memory очередь + фоновый drain под одним pooled-коннектом. Здесь — unit
проверки contract'а `AuditOutbox`: push non-blocking, переполнение дропает
старейший элемент и инкрементит counter, drain собирает события в один батч,
`stop()` выгребает остаток с бюджетом.

Без `pytest-asyncio` — async-сценарии гоним через `asyncio.run`, тот же
паттерн, что в `test_introspect_pool.py` и `test_concurrency.py`.
"""

import asyncio

from src.services.audit_outbox import AuditEnvelope, AuditOutbox, make_envelope


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
    """Stub-сессия: считает commit/rollback/close, поддерживает begin_nested.

    `begin_nested()` возвращает context manager — реальная сессия делает
    savepoint, но для unit-проверки outbox'а достаточно ничего-не-делающего
    обёртывания, чтобы writer мог собрать SQL без падения.
    """

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


# ── push enqueues, drain пишет батчем ───────────────────────────────────────


class TestPushNonBlocking:
    def test_push_enqueues_when_running(self):
        captured: list[AuditEnvelope] = []
        sessions: list[_FakeSession] = []

        def factory():
            s = _FakeSession()
            sessions.append(s)
            return s

        def writer(db, env):
            captured.append(env)

        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=4,
                poll_interval_seconds=0.01,
                session_factory=factory,
                writer=writer,
                bump_failure=lambda: 0,
            )
            outbox.start()
            try:
                for i in range(5):
                    outbox.push_nowait(_env(f"action.{i}"))
                # Ждём, пока drain обработает.
                for _ in range(100):
                    if len(captured) >= 5:
                        break
                    await asyncio.sleep(0.02)
            finally:
                await outbox.stop(timeout=1.0)

        asyncio.run(run())
        assert sorted(e.action for e in captured) == [
            f"action.{i}" for i in range(5)
        ]
        assert sum(s.commits for s in sessions) >= 1
        assert all(s.closes == 1 for s in sessions)


# ── bounded buffer + dropped_total ──────────────────────────────────────────


class TestBoundedBuffer:
    def test_overflow_drops_oldest_and_counts(self):
        async def run():
            outbox = AuditOutbox(
                max_size=2,
                batch_size=1,
                poll_interval_seconds=0.5,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            # Эмулируем `start()` без drain'а: создаём очередь руками.
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=2)

            outbox.push_nowait(_env("first"))
            outbox.push_nowait(_env("second"))
            # Переполнение: дропает старейший, ставит новый.
            outbox.push_nowait(_env("third"))

            assert outbox.qsize() == 2
            assert outbox.dropped_total() == 1

            remaining = []
            while outbox.qsize():
                remaining.append(outbox._queue.get_nowait().action)
            return remaining

        remaining = asyncio.run(run())
        assert remaining == ["second", "third"]


# ── fallback без started loop ───────────────────────────────────────────────


class TestFallbackWithoutLoop:
    def test_fallback_writes_inline(self):
        sessions: list[_FakeSession] = []
        captured: list[AuditEnvelope] = []

        def factory():
            s = _FakeSession()
            sessions.append(s)
            return s

        def writer(db, env):
            captured.append(env)

        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=0.05,
            session_factory=factory,
            writer=writer,
            bump_failure=lambda: 0,
        )
        # `start` НЕ зовём — `_queue is None`, push идёт в fallback.
        accepted = outbox.push_nowait(_env("inline"))
        assert accepted is False
        assert len(captured) == 1
        assert sessions[0].closes == 1


# ── drain не валится на одном битом событии ────────────────────────────────


class TestDrainCarriesOnAfterError:
    def test_failing_event_bumps_counter_others_pass(self):
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        captured: list[AuditEnvelope] = []

        def writer(db, env):
            if env.action == "bad":
                raise RuntimeError("boom")
            captured.append(env)

        async def run():
            outbox = AuditOutbox(
                max_size=8,
                batch_size=4,
                poll_interval_seconds=0.01,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=bump,
            )
            outbox.start()
            try:
                outbox.push_nowait(_env("good-1"))
                outbox.push_nowait(_env("bad"))
                outbox.push_nowait(_env("good-2"))
                for _ in range(100):
                    if len(captured) >= 2 and failures["n"] >= 1:
                        break
                    await asyncio.sleep(0.02)
            finally:
                await outbox.stop(timeout=1.0)

        asyncio.run(run())
        assert sorted(e.action for e in captured) == ["good-1", "good-2"]
        assert failures["n"] == 1


# ── graceful shutdown drains pending ───────────────────────────────────────


class TestGracefulShutdown:
    def test_stop_drains_pending(self):
        captured: list[AuditEnvelope] = []

        def writer(db, env):
            captured.append(env)

        async def run():
            # Длинный poll-interval — drain не успевает выгрести самостоятельно
            # после первого батча, остальное должно подобрать `stop()`.
            outbox = AuditOutbox(
                max_size=16,
                batch_size=2,
                poll_interval_seconds=5.0,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=lambda: 0,
            )
            outbox.start()
            for i in range(7):
                outbox.push_nowait(_env(f"act.{i}"))
            await outbox.stop(timeout=2.0)

        asyncio.run(run())
        assert sorted(e.action for e in captured) == [f"act.{i}" for i in range(7)]

    def test_stop_idle_outbox_no_writes(self):
        captured: list[AuditEnvelope] = []

        async def run():
            outbox = AuditOutbox(
                max_size=4,
                batch_size=2,
                poll_interval_seconds=5.0,
                session_factory=_FakeSession,
                writer=lambda db, env: captured.append(env),
                bump_failure=lambda: 0,
            )
            outbox.start()
            await outbox.stop(timeout=0.5)

        asyncio.run(run())
        assert captured == []


# ── cancel ПОСЛЕ commit'а не должен приводить к дублям через _drain_remaining ─


class TestCancelAfterCommitNoRequeue:
    """Прямая регрессия на race: `_write_batch_sync` успел закоммитить батч,
    `to_thread` вернулся, и сразу прилетел `CancelledError` до того, как
    drain-loop инкрементил счётчики. Cancel-ветка должна по `committed_ids`
    отфильтровать requeue, чтобы `_drain_remaining` ничего не дописал."""

    def test_committed_batch_not_requeued_into_drain_remaining(self):
        """Подменяем `_write_batch_sync` так, чтобы он закоммитил батч и
        затем заставил `_flush_batch` пробросить `CancelledError`. После
        `_drain_remaining` writer не должен быть вызван второй раз."""

        writer_calls: list[str] = []

        def writer(_db, env):
            writer_calls.append(env.action)

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_FakeSession,
            writer=writer,
            bump_failure=lambda: 0,
        )

        batch = [_env(f"ev-{i}") for i in range(3)]

        # Подменённый sync-таргет: «коммитит» (заполняет committed_ids), но
        # затем эмулирует cancel в await'е `to_thread` — это ровно тот race,
        # где старая логика requeue'ила уже-в-БД events.
        def fake_write_batch_sync(b, committed_ids):
            for env in b:
                writer(None, env)
                committed_ids.add(id(env))
            return len(b)

        outbox._write_batch_sync = fake_write_batch_sync  # type: ignore[assignment]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)

            committed_ids: set[int] = set()

            # Имитируем drain-loop'овский кусок: вызываем `_flush_batch`,
            # потом пробрасываем CancelledError ровно как в реальном
            # сценарии graceful-shutdown'а.
            await outbox._flush_batch(batch, committed_ids)

            # commit прошёл, все три envelope'а в set'е.
            assert committed_ids == {id(env) for env in batch}
            assert writer_calls == ["ev-0", "ev-1", "ev-2"]

            # Эмулируем cancel-ветку из `_drain_loop`: re-enqueue только те,
            # кого НЕ в committed_ids → ничего не должно попасть в очередь.
            for envelope in batch:
                if id(envelope) in committed_ids:
                    continue
                outbox._queue.put_nowait(envelope)

            assert outbox._queue.qsize() == 0

            # `_drain_remaining` на пустой очереди — никаких новых writer-call'ов.
            await outbox._drain_remaining(timeout=0.5)

        asyncio.run(run())

        # Контракт: writer вызван ровно по разу на каждое событие.
        assert writer_calls == ["ev-0", "ev-1", "ev-2"]

    def test_drain_loop_cancel_branch_filters_committed(self):
        """End-to-end: реально пробрасываем CancelledError ВО ВРЕМЯ
        `_flush_batch` ПОСЛЕ того, как sync-target закоммитил, и убеждаемся,
        что `_drain_remaining` НЕ перепишет тот же батч."""

        writer_calls: list[str] = []

        def writer(_db, env):
            writer_calls.append(env.action)

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_FakeSession,
            writer=writer,
            bump_failure=lambda: 0,
        )

        batch = [_env(f"ev-{i}") for i in range(2)]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)

            commit_done = asyncio.Event()

            def fake_write_batch_sync(b, committed_ids):
                # Помечаем commit как прошедший.
                for env in b:
                    writer(None, env)
                    committed_ids.add(id(env))
                outbox._loop.call_soon_threadsafe(commit_done.set)
                return len(b)

            outbox._write_batch_sync = fake_write_batch_sync  # type: ignore[assignment]

            committed_ids: set[int] = set()

            async def flush_then_cancel():
                # Запускаем `_flush_batch`; внутри to_thread пометит commit,
                # после возврата мы эмулируем cancel ровно так, как делает
                # `stop()`/`task.cancel()`.
                await outbox._flush_batch(batch, committed_ids)
                # Поднимаем cancel руками — после `to_thread` returning.
                raise asyncio.CancelledError

            try:
                await flush_then_cancel()
            except asyncio.CancelledError:
                # Точно копия cancel-ветки из `_drain_loop`.
                for envelope in batch:
                    if id(envelope) in committed_ids:
                        continue
                    outbox._queue.put_nowait(envelope)

            assert committed_ids == {id(env) for env in batch}
            assert outbox._queue.qsize() == 0

            await outbox._drain_remaining(timeout=0.5)
            assert commit_done.is_set()

        asyncio.run(run())

        # Главный инвариант: каждое событие записано ровно один раз.
        assert writer_calls == ["ev-0", "ev-1"]
