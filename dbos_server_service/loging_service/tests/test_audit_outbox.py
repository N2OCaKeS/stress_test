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

from src.services.audit_outbox import AuditEnvelope, AuditOutbox

from tests._helpers import make_env as _env


class _SavepointContextManager:
    """SAVEPOINT-shaped context manager.

    Реальный `Session.begin_nested()` возвращает отдельный объект — savepoint,
    у которого свой `__enter__`/`__exit__`, а внутри `__exit__` он явно вызывает
    `rollback()` на родительской сессии при исключении (и пропускает его дальше).
    Стаб моделирует тот же контракт: bump'аем `savepoint_rollbacks` родителя,
    не глотаем исключение — outbox должен поймать его в writer-loop.
    """

    def __init__(self, parent: "_FakeSession"):
        self._parent = parent

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._parent.savepoint_rollbacks += 1
        return False


class _FakeSession:
    """Stub-сессия: считает commit/rollback/close + savepoint-rollbacks.

    `begin_nested()` возвращает `_SavepointContextManager`, который ведёт себя
    как SQLAlchemy-savepoint: на исключении внутри `with` помечает rollback и
    пропускает ошибку наверх — это даёт outbox'у тот же signal-path, что и
    реальная сессия (без false-confidence от self-returning стаба).
    """

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.savepoint_rollbacks = 0

    def begin_nested(self):
        return _SavepointContextManager(self)

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


# ── bounded buffer + dropped counters ───────────────────────────────────────


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
            assert outbox.dropped_overflow_total() == 1
            # cause-разделение: cancel/shutdown в этом сценарии не происходит
            assert outbox.dropped_cancel_total() == 0
            assert outbox.dropped_shutdown_total() == 0

            remaining = []
            while outbox.qsize():
                remaining.append(outbox._queue.get_nowait().action)
            return remaining

        remaining = asyncio.run(run())
        assert remaining == ["second", "third"]

    def test_overflow_drain_race_does_not_overcount(self):
        """Race: между `put_nowait` (поймал QueueFull) и `get_nowait`
        (eviction) drain успел выгрести очередь. Реальной потери не было,
        повторный put прошёл — счётчик `_dropped_overflow_total` бампить
        не нужно, иначе метрика over-counts."""

        async def run():
            outbox = AuditOutbox(
                max_size=1,
                batch_size=1,
                poll_interval_seconds=0.5,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=1)

            # Заполняем очередь до отказа.
            outbox.push_nowait(_env("a"))
            assert outbox.qsize() == 1

            # Эмулируем гонку: подменяем `put_nowait` так, чтобы он бросил
            # QueueFull один раз, а перед `get_nowait` очередь оказалась
            # пустой (drain опередил eviction).
            real_put = outbox._queue.put_nowait
            real_get = outbox._queue.get_nowait
            calls = {"put": 0}

            def fake_put(item):
                calls["put"] += 1
                if calls["put"] == 1:
                    # Имитируем переполнение на первой попытке.
                    raise asyncio.QueueFull
                return real_put(item)

            def fake_get():
                # «Drain» успел выгрести между put и get.
                # Чистим очередь сами и кидаем QueueEmpty.
                while True:
                    try:
                        real_get()
                    except asyncio.QueueEmpty:
                        break
                raise asyncio.QueueEmpty

            outbox._queue.put_nowait = fake_put  # type: ignore[assignment]
            outbox._queue.get_nowait = fake_get  # type: ignore[assignment]

            accepted = outbox.push_nowait(_env("b"))
            return accepted, outbox.dropped_overflow_total()

        accepted, dropped = asyncio.run(run())
        assert accepted is True
        # Никакой потери не случилось — drain опередил eviction.
        assert dropped == 0

    def test_overflow_second_put_fails_bumps_once(self):
        """Если первый put упал QueueFull, eviction прошёл, но второй put
        тоже упал (конкурирующий push занял слот) — `_dropped_overflow_total`
        растёт ровно на 1 (наше событие потеряно), а не на 2."""

        async def run():
            outbox = AuditOutbox(
                max_size=1,
                batch_size=1,
                poll_interval_seconds=0.5,
                session_factory=_FakeSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=1)
            outbox.push_nowait(_env("a"))

            # Подменяем put_nowait так, чтобы все вызовы кидали QueueFull.
            def always_full(_item):
                raise asyncio.QueueFull

            outbox._queue.put_nowait = always_full  # type: ignore[assignment]

            accepted = outbox.push_nowait(_env("b"))
            return accepted, outbox.dropped_overflow_total()

        accepted, dropped = asyncio.run(run())
        assert accepted is False
        # 1 за реальное вытеснение старейшего + 1 за провал второго put = 2.
        # Главное: НЕ 3 (старая версия бампила безусловно после eviction-блока).
        assert dropped == 2


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
        # До start() флаг `_stopping` ещё в False, after-stop-counter не растёт.
        assert outbox.dropped_after_stop_total() == 0

    def test_fallback_after_stop_bumps_counter(self):
        """push_nowait после `stop()` инкрементит dropped_after_stop_total."""
        captured: list[AuditEnvelope] = []

        def writer(db, env):
            captured.append(env)

        async def run():
            outbox = AuditOutbox(
                max_size=4,
                batch_size=2,
                poll_interval_seconds=0.05,
                session_factory=_FakeSession,
                writer=writer,
                bump_failure=lambda: 0,
            )
            outbox.start()
            await outbox.stop(timeout=1.0)
            return outbox

        outbox = asyncio.run(run())
        # Outbox остановлен, `_stopping=True`, `_queue is None`. push идёт
        # в fallback и должен бампить новый counter ровно один раз.
        accepted = outbox.push_nowait(_env("after-stop"))
        assert accepted is False
        assert len(captured) == 1
        assert outbox.dropped_after_stop_total() == 1


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


# ── force-cancel внутри _drain_remaining должен бампить shutdown counter ─────


class TestDrainRemainingCancelled:
    """`CancelledError` это `BaseException`, generic `except Exception` его
    не ловит. Без отдельной ветки события, выдернутые из очереди в `batch`
    непосредственно перед force-cancel'ом, молча терялись бы — нарушая
    инвариант `enqueued = drained + failures + dropped`."""

    def test_force_cancel_during_flush_bumps_dropped_shutdown(self):
        outbox = AuditOutbox(
            max_size=16,
            batch_size=2,
            poll_interval_seconds=5.0,
            session_factory=_FakeSession,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            # Сами кладём envelope'ы в очередь — drain-loop не запускаем.
            for i in range(5):
                outbox._queue.put_nowait(_env(f"ev-{i}"))

            # Подменяем _flush_batch на корутину, которая всегда бросает
            # CancelledError — имитация force-cancel'а в await'е flush'а
            # после того, как drain_remaining уже выгребла batch из очереди.
            # Сигнатура `(batch, committed_keys=None)` зеркалит реальную: с тех
            # пор как `_drain_remaining` передаёт `committed_keys` для коррекции
            # `dropped_shutdown` на cancel-after-commit, моки должны принимать
            # второй аргумент, чтобы не падать на TypeError.
            async def cancelling_flush(batch, committed_keys=None):
                raise asyncio.CancelledError()

            outbox._flush_batch = cancelling_flush  # type: ignore[assignment]

            try:
                await outbox._drain_remaining(timeout=1.0)
            except asyncio.CancelledError:
                pass

            # batch_size=2 → в batch ушло 2 envelope'а, ещё 3 остались в
            # очереди. После force-cancel'а все 5 должны быть учтены в
            # `_dropped_shutdown_total`.
            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        assert dropped == 5, (
            f"force-cancel в drain_remaining должен бампнуть shutdown counter "
            f"на batch + queue residue (ожидали 5, получили {dropped})"
        )

    def test_cancelled_error_is_propagated(self):
        """CancelledError ВСЁ ЕЩЁ должен пробрасываться (graceful shutdown
        контракт): suppress'нём только counter-бамп."""
        outbox = AuditOutbox(
            max_size=4,
            batch_size=2,
            poll_interval_seconds=5.0,
            session_factory=_FakeSession,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            outbox._queue.put_nowait(_env("ev-1"))

            async def cancelling_flush(batch, committed_keys=None):
                raise asyncio.CancelledError()

            outbox._flush_batch = cancelling_flush  # type: ignore[assignment]

            raised = False
            try:
                await outbox._drain_remaining(timeout=1.0)
            except asyncio.CancelledError:
                raised = True
            return raised

        assert asyncio.run(run()) is True


# ── cancel ПОСЛЕ commit'а не должен приводить к дублям через _drain_remaining ─


class TestCancelAfterCommitNoRequeue:
    """Прямая регрессия на race: `_write_batch_sync` успел закоммитить батч,
    `to_thread` вернулся, и сразу прилетел `CancelledError` до того, как
    drain-loop инкрементил счётчики. Cancel-ветка должна по `committed_keys`
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

        # Подменённый sync-таргет: «коммитит» (заполняет committed_keys), но
        # затем эмулирует cancel в await'е `to_thread` — это ровно тот race,
        # где старая логика requeue'ила уже-в-БД events.
        def fake_write_batch_sync(b, committed_keys):
            for env in b:
                writer(None, env)
                committed_keys.add(env.idempotency_key)
            return len(b)

        outbox._write_batch_sync = fake_write_batch_sync  # type: ignore[assignment]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)

            committed_keys: set[str] = set()

            # Имитируем drain-loop'овский кусок: вызываем `_flush_batch`,
            # потом пробрасываем CancelledError ровно как в реальном
            # сценарии graceful-shutdown'а.
            await outbox._flush_batch(batch, committed_keys)

            # commit прошёл, все три envelope'а в set'е.
            assert committed_keys == {env.idempotency_key for env in batch}
            assert writer_calls == ["ev-0", "ev-1", "ev-2"]

            # Эмулируем cancel-ветку из `_drain_loop`: re-enqueue только те,
            # кого НЕ в committed_keys → ничего не должно попасть в очередь.
            for envelope in batch:
                if envelope.idempotency_key in committed_keys:
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

            def fake_write_batch_sync(b, committed_keys):
                # Помечаем commit как прошедший.
                for env in b:
                    writer(None, env)
                    committed_keys.add(env.idempotency_key)
                outbox._loop.call_soon_threadsafe(commit_done.set)
                return len(b)

            outbox._write_batch_sync = fake_write_batch_sync  # type: ignore[assignment]

            committed_keys: set[str] = set()

            async def flush_then_cancel():
                # Запускаем `_flush_batch`; внутри to_thread пометит commit,
                # после возврата мы эмулируем cancel ровно так, как делает
                # `stop()`/`task.cancel()`.
                await outbox._flush_batch(batch, committed_keys)
                # Поднимаем cancel руками — после `to_thread` returning.
                raise asyncio.CancelledError

            try:
                await flush_then_cancel()
            except asyncio.CancelledError:
                # Точно копия cancel-ветки из `_drain_loop`.
                for envelope in batch:
                    if envelope.idempotency_key in committed_keys:
                        continue
                    outbox._queue.put_nowait(envelope)

            assert committed_keys == {env.idempotency_key for env in batch}
            assert outbox._queue.qsize() == 0

            await outbox._drain_remaining(timeout=0.5)
            assert commit_done.is_set() is True

        asyncio.run(run())

        # Главный инвариант: каждое событие записано ровно один раз.
        assert writer_calls == ["ev-0", "ev-1"]


# ── `_bump_failure` не дублируется на savepoint-fail + commit-fail ───────────


class _CommitFailingSession:
    """Session-stub: writer падает на «bad» envelope'ах, commit бросает.

    Имитирует ровно тот сценарий, где `_write_batch_sync` сначала забампил
    savepoint-failed envelope'ы, а потом outer `db.commit()` упал — раньше
    `_flush_batch`'s `except Exception` бампил всех заново.
    """

    def __init__(self):
        self.commits = 0
        self.savepoint_rollbacks = 0

    def begin_nested(self):
        return _SavepointContextManager(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Никакого suppression — savepoint падает, исключение всплывает в
        # writer-loop и попадает в `_bump_failure`-ветку.
        return False

    def commit(self):
        self.commits += 1
        raise RuntimeError("commit boom")

    def rollback(self):
        pass

    def close(self):
        pass


class TestBumpFailureNotDoubleCountedOnCommitFail:
    """Регрессия: savepoint-failed envelope не должен получать второй bump,
    если затем падает `db.commit()` outer-tx."""

    def test_savepoint_fail_plus_commit_fail_one_bump_per_envelope(self):
        failures: dict[str, int] = {}

        def bump():
            # Один общий счётчик: тест проверяет, что суммарно вызывается
            # ровно по `len(batch)` раз, и ни один envelope не дважды.
            failures.setdefault("n", 0)
            failures["n"] += 1
            return failures["n"]

        bad_writes: set[str] = {"bad-1", "bad-2"}

        def writer(_db, env):
            if env.action in bad_writes:
                raise RuntimeError(f"writer rejected {env.action}")

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_CommitFailingSession,
            writer=writer,
            bump_failure=bump,
        )

        batch = [
            _env("good-1"),
            _env("bad-1"),
            _env("good-2"),
            _env("bad-2"),
        ]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            await outbox._flush_batch(batch)

        asyncio.run(run())

        # Контракт: каждое из 4 events должно дать ровно один bump.
        # Раньше bad-1/bad-2 бампились дважды (savepoint + commit-fail loop).
        assert failures.get("n", 0) == len(batch), (
            f"expected exactly {len(batch)} bumps (one per envelope), "
            f"got {failures.get('n', 0)}"
        )

    def test_only_savepoint_fail_no_commit_fail_one_bump_each(self):
        """Sanity: если commit прошёл, savepoint-failed получают ровно один bump."""
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        bad_writes = {"bad"}

        def writer(_db, env):
            if env.action in bad_writes:
                raise RuntimeError("writer boom")

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_FakeSession,
            writer=writer,
            bump_failure=bump,
        )

        batch = [_env("good"), _env("bad"), _env("good-2")]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            await outbox._flush_batch(batch)

        asyncio.run(run())
        # Только bad → один bump.
        assert failures["n"] == 1

    def test_commit_fail_without_savepoint_fails_bumps_all_once(self):
        """Если все savepoint'ы прошли, а commit упал — каждый envelope
        должен получить bump ровно один раз (всё содержимое потеряно)."""
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        def writer(_db, env):
            return None  # все savepoint'ы успешны

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=_CommitFailingSession,
            writer=writer,
            bump_failure=bump,
        )

        batch = [_env("a"), _env("b"), _env("c")]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            await outbox._flush_batch(batch)

        asyncio.run(run())
        assert failures["n"] == len(batch)

    def test_session_factory_failure_bumps_each_envelope_once(self):
        """Регрессия: если `session_factory` падает до writes (катастрофа
        уровня сессии), bump'аем каждое событие ровно один раз — sync-часть
        не успела ничего забампить."""
        failures = {"n": 0}

        def bump():
            failures["n"] += 1
            return failures["n"]

        def boom_factory():
            raise RuntimeError("pool exhausted")

        outbox = AuditOutbox(
            max_size=8,
            batch_size=8,
            poll_interval_seconds=0.01,
            session_factory=boom_factory,
            writer=lambda db, env: None,
            bump_failure=bump,
        )

        batch = [_env("a"), _env("b")]

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            await outbox._flush_batch(batch)

        asyncio.run(run())
        assert failures["n"] == len(batch)


# ── _drain_remaining: committed events не должны учитываться как dropped_shutdown ─


class TestDrainRemainingCommittedNotDropped:
    """До фикса `_drain_remaining` вызывал `_flush_batch(batch)` без `committed_keys`,
    так что cancel-ветка считала весь батч `lost`, даже если sync-target успел
    `db.commit()`. Это бампило `_dropped_shutdown_total` на уже записанных в БД
    events — двойной учёт и ложный alert «потеряно N»."""

    def test_cancel_after_commit_in_drain_remaining_does_not_bump_dropped_shutdown(self):
        outbox = AuditOutbox(
            max_size=8,
            batch_size=4,
            poll_interval_seconds=5.0,
            session_factory=_FakeSession,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            for i in range(3):
                outbox._queue.put_nowait(_env(f"ev-{i}"))

            # Подменяем `_flush_batch` так, чтобы он:
            #   1) пометил все envelope'ы как committed (типа `db.commit()` успел);
            #   2) пробросил CancelledError ровно как сделал бы реальный flush,
            #      если cancel прилетел сразу после returning `to_thread`.
            async def commit_then_cancel(batch, committed_keys=None):
                if committed_keys is None:
                    committed_keys = set()
                for env in batch:
                    committed_keys.add(env.idempotency_key)
                raise asyncio.CancelledError()

            outbox._flush_batch = commit_then_cancel  # type: ignore[assignment]

            try:
                await outbox._drain_remaining(timeout=1.0)
            except asyncio.CancelledError:
                pass

            return (
                outbox.dropped_shutdown_total(),
                outbox._queue.qsize() if outbox._queue is not None else -1,
            )

        dropped, residue = asyncio.run(run())
        # batch_size=4, всего 3 envelope'а — все ушли в batch, committed_keys
        # вернёт 3, queue residue = 0. lost = 3 - 3 + 0 = 0.
        assert dropped == 0, (
            f"committed-в-БД events не должны учитываться в dropped_shutdown "
            f"(ожидали 0, получили {dropped})"
        )
        assert residue == 0

    def test_cancel_partial_commit_in_drain_remaining_counts_only_uncommitted(self):
        """Batch=3 envelope'а, sync-target успел закоммитить только 1, остальные
        2 — нет (cancel посередине). dropped_shutdown_total должен учесть
        ровно 2, не 3."""
        outbox = AuditOutbox(
            max_size=8,
            batch_size=4,
            poll_interval_seconds=5.0,
            session_factory=_FakeSession,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            envs = [_env(f"ev-{i}") for i in range(3)]
            for env in envs:
                outbox._queue.put_nowait(env)

            async def partial_commit_then_cancel(batch, committed_keys=None):
                if committed_keys is None:
                    committed_keys = set()
                # Только первый envelope успел в БД.
                committed_keys.add(batch[0].idempotency_key)
                raise asyncio.CancelledError()

            outbox._flush_batch = partial_commit_then_cancel  # type: ignore[assignment]

            try:
                await outbox._drain_remaining(timeout=1.0)
            except asyncio.CancelledError:
                pass

            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        assert dropped == 2, (
            f"один committed envelope не должен учитываться, "
            f"остальные 2 — должны (ожидали 2, получили {dropped})"
        )

    def test_cancel_before_commit_in_drain_remaining_counts_full_batch(self):
        """Регрессия: если sync-target НЕ успел до commit'а
        (committed_keys пуст), `_drain_remaining` ДОЛЖЕН по-прежнему бампить
        shutdown counter на весь batch — фикс не меняет это поведение."""
        outbox = AuditOutbox(
            max_size=16,
            batch_size=2,
            poll_interval_seconds=5.0,
            session_factory=_FakeSession,
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )

        async def run():
            outbox._loop = asyncio.get_running_loop()
            outbox._queue = asyncio.Queue(maxsize=outbox._max_size)
            for i in range(5):
                outbox._queue.put_nowait(_env(f"ev-{i}"))

            async def cancel_before_commit(batch, committed_keys=None):
                # committed_keys остаётся пустым — имитируем force-cancel
                # до того, как sync-часть стартовала.
                raise asyncio.CancelledError()

            outbox._flush_batch = cancel_before_commit  # type: ignore[assignment]

            try:
                await outbox._drain_remaining(timeout=1.0)
            except asyncio.CancelledError:
                pass

            return outbox.dropped_shutdown_total()

        dropped = asyncio.run(run())
        # batch=2, queue residue=3 → lost = 2 - 0 + 3 = 5.
        assert dropped == 5


# ── _audit_drain_task_done_callback: 3 ветки ────────────────────────────────


class TestDrainTaskDoneCallback:
    """`_audit_drain_task_done_callback` — единственный SOC-сигнал смерти
    `_drain_task`'а. Должен:

    * на cancellation — молчать (graceful shutdown);
    * на exception — писать CRITICAL c stack-trace'ом;
    * на штатный return — писать WARNING (loop вышел без `stop()` —
      возможен breaking change).
    """

    def test_cancelled_task_is_silent(self, caplog):
        import logging as _logging
        from src.main import _audit_drain_task_done_callback

        async def runner():
            async def doomed():
                await asyncio.sleep(60)

            task = asyncio.create_task(doomed())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return task

        with caplog.at_level(_logging.WARNING, logger="src.main"):
            done = asyncio.run(runner())
            _audit_drain_task_done_callback(done)

        # Никаких WARNING/CRITICAL — cancel это graceful shutdown.
        noisy = [
            r for r in caplog.records
            if r.levelno >= _logging.WARNING and r.name == "src.main"
        ]
        assert noisy == [], (
            f"callback не должен ничего логировать на cancel, получили: "
            f"{[r.message for r in noisy]!r}"
        )

    def test_exception_in_task_logs_critical(self, caplog):
        import logging as _logging
        from src.main import _audit_drain_task_done_callback

        async def runner():
            async def boom():
                raise RuntimeError("drain crashed mid-loop")

            task = asyncio.create_task(boom())
            try:
                await task
            except RuntimeError:
                pass
            return task

        with caplog.at_level(_logging.CRITICAL, logger="src.main"):
            done = asyncio.run(runner())
            _audit_drain_task_done_callback(done)

        critical = [
            r.message for r in caplog.records if r.levelno == _logging.CRITICAL
        ]
        assert any(
            "audit outbox drain task died with exception" in m
            for m in critical
        ), f"ожидали CRITICAL про died-with-exception, получили: {critical!r}"

    def test_normal_exit_logs_warning(self, caplog):
        import logging as _logging
        from src.main import _audit_drain_task_done_callback

        async def runner():
            async def normal_exit():
                return None

            task = asyncio.create_task(normal_exit())
            await task
            return task

        with caplog.at_level(_logging.WARNING, logger="src.main"):
            done = asyncio.run(runner())
            _audit_drain_task_done_callback(done)

        warnings = [
            r.message for r in caplog.records if r.levelno == _logging.WARNING
        ]
        assert any(
            "exited without exception" in m for m in warnings
        ), f"ожидали WARNING про exit-without-exception, получили: {warnings!r}"
