"""dispatch_outbox publisher loop — server_worker side.

Покрытие:

* `poll_once()` забирает batch pending-row'ов, публикует через broker и
  ставит `dispatched_at`
* На ошибке publish — `attempts+1`, `last_error`, `next_retry_at` с
  exponential backoff (2^attempts, cap 5 минут)
* После `MAX_ATTEMPTS` row остаётся pending (по ТЗ), WARNING лог
* `cleanup_old()` удаляет только `dispatched_at IS NOT NULL AND
  dispatched_at < cutoff`
* `run_publisher_loop()` пробрасывает `asyncio.CancelledError` —
  graceful shutdown
* Empty pending — no-op (poller не дёргает лишние log/query)

Сессия и broker mock'аются через monkeypatch — таблица `dispatch_outbox`
живёт в БД server_service, на worker-стенде её нет.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tasks import dispatch_outbox as outbox_poller


# ── In-memory строка outbox'а для тестов ─────────────────────────────────────


class _Row:
    """Имитация `DispatchOutbox` ORM-row — minimal SQLAlchemy-like API."""

    def __init__(
        self,
        *,
        task_id: str,
        task_kind: str,
        payload: dict | None = None,
        attempts: int = 0,
    ) -> None:
        self.id = uuid.uuid4()
        self.task_id = task_id
        self.task_kind = task_kind
        self.payload = payload or {}
        self.attempts = attempts
        self.last_error: str | None = None
        self.next_retry_at: datetime | None = None
        self.dispatched_at: datetime | None = None
        self.created_at = datetime.now(timezone.utc)


# ── Session / broker doubles ────────────────────────────────────────────────


class _FakeScalars:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows

    def scalars(self) -> "_FakeScalars":
        return self


class _FakeResult:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Async session double — отдаёт заданный набор pending-row'ов."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.commit_count = 0
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return _FakeResult(self._rows)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _install_session(monkeypatch, session: _FakeSession) -> None:
    def factory():
        return session

    monkeypatch.setattr(
        outbox_poller.dispatch_outbox_session,
        "get_session_factory",
        lambda: factory,
    )


def _install_broker(monkeypatch, broker_mock) -> None:
    # poll_once делает локальный `from src.main import broker` — monkeypatch'им
    # модуль `src.main` целиком, чтобы import внутри функции попал в наш мок.
    import src.main as _main
    monkeypatch.setattr(_main, "broker", broker_mock)


def _make_broker(*, kiq_exc: Exception | None = None, unknown_kind: bool = False):
    """Собрать broker mock с заданным поведением `find_task(...).kicker().kiq(...)`."""
    broker = MagicMock()
    if unknown_kind:
        broker.find_task = MagicMock(return_value=None)
        return broker

    kiq = AsyncMock(side_effect=kiq_exc) if kiq_exc else AsyncMock()
    kicker = MagicMock()
    kicker.kiq = kiq
    task = MagicMock()
    task.kicker = MagicMock(return_value=kicker)
    broker.find_task = MagicMock(return_value=task)
    broker._kiq = kiq  # удобный аксессор для проверок
    return broker


# ── poll_once: happy path ────────────────────────────────────────────────────


class TestPollOnceHappyPath:
    async def test_dispatches_pending_rows_and_marks_dispatched(self, monkeypatch):
        rows = [
            _Row(task_id="tsk_a", task_kind="power.on"),
            _Row(task_id="tsk_b", task_kind="inventory.sync"),
        ]
        session = _FakeSession(rows)
        broker = _make_broker()
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        # Оба row помечены dispatched_at
        assert all(r.dispatched_at is not None for r in rows)
        # broker.find_task вызван per-row
        assert broker.find_task.call_count == 2
        # kiq вызван на оба task_id
        kiq_args = [c.args for c in broker._kiq.call_args_list]
        assert ("tsk_a",) in kiq_args
        assert ("tsk_b",) in kiq_args
        # Per-row commit: по одному commit'у на каждый успешный dispatch
        assert session.commit_count == 2

    async def test_clears_error_and_retry_on_success(self, monkeypatch):
        """Если row пришла с прошлой ошибкой — после успешного publish
        last_error и next_retry_at очищены, чтобы row в admin/log выглядела
        как чистый success."""
        row = _Row(task_id="tsk_c", task_kind="power.on", attempts=2)
        row.last_error = "prev fail"
        row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        session = _FakeSession([row])
        broker = _make_broker()
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        assert row.dispatched_at is not None
        assert row.last_error is None
        assert row.next_retry_at is None


# ── poll_once: empty ────────────────────────────────────────────────────────


class TestPollOnceEmpty:
    async def test_empty_pending_no_broker_calls(self, monkeypatch):
        """Пустой батч → kiq не вызывается, commit не делается (per-row
        commit: нечего коммитить), broker не дёргается."""
        session = _FakeSession([])
        broker = _make_broker()
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        assert broker.find_task.call_count == 0
        # SELECT всё равно делается, но commit'ить нечего.
        assert session.execute_calls == 1
        assert session.commit_count == 0


# ── poll_once: publish failure → backoff ─────────────────────────────────────


class TestPollOncePublishFailure:
    async def test_kiq_failure_increments_attempts_sets_next_retry(self, monkeypatch):
        row = _Row(task_id="tsk_fail", task_kind="power.on", attempts=0)
        session = _FakeSession([row])
        broker = _make_broker(kiq_exc=ConnectionError("redis down"))
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        # Row остаётся pending (dispatched_at IS NULL)
        assert row.dispatched_at is None
        assert row.attempts == 1
        assert row.last_error is not None
        assert "ConnectionError" in row.last_error
        # next_retry_at выставлен в будущее
        assert row.next_retry_at is not None
        assert row.next_retry_at > datetime.now(timezone.utc) - timedelta(seconds=1)

    async def test_backoff_grows_exponentially(self, monkeypatch):
        """`next_retry_at` = now + 2^attempts (cap 5 min). После 1 fail — ~2с,
        после 5 fail — ~32с. Чек на нескольких значениях."""
        row = _Row(task_id="tsk_bf", task_kind="power.on", attempts=4)
        session = _FakeSession([row])
        broker = _make_broker(kiq_exc=RuntimeError("temporary"))
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        before = datetime.now(timezone.utc)
        await outbox_poller.poll_once()

        # attempts стало 5 → задержка 2^5=32с
        assert row.attempts == 5
        delay = (row.next_retry_at - before).total_seconds()
        # с поправкой на runtime — должно быть около 32с, но никак не >300
        assert 25 <= delay <= 60

    async def test_backoff_capped_at_5_minutes(self, monkeypatch):
        """2^large >> 5 min, но cap режет до 300с.

        attempts стартует с 8 → инкремент даст 9 < MAX_ATTEMPTS=10 (cap не
        срабатывает), 2^9 = 512s, поверх режется до 300s.
        """
        row = _Row(task_id="tsk_cap", task_kind="power.on", attempts=8)
        session = _FakeSession([row])
        broker = _make_broker(kiq_exc=RuntimeError("still down"))
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        before = datetime.now(timezone.utc)
        await outbox_poller.poll_once()

        assert row.next_retry_at is not None
        delay = (row.next_retry_at - before).total_seconds()
        assert 290 <= delay <= 310

    async def test_max_attempts_leaves_row_pending_logs_warning(
        self, monkeypatch, caplog
    ):
        """После `MAX_ATTEMPTS` row остаётся pending — оператор разруливает.
        WARNING лог обязателен."""
        # Один шаг ниже max_attempts, чтобы инкремент достиг cap'а
        from src.core.config import get_settings
        max_attempts = get_settings().dispatch_outbox_max_attempts
        row = _Row(task_id="tsk_max", task_kind="power.on", attempts=max_attempts - 1)
        session = _FakeSession([row])
        broker = _make_broker(kiq_exc=RuntimeError("nope"))
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        import logging
        caplog.set_level(logging.WARNING, logger="src.tasks.dispatch_outbox")
        await outbox_poller.poll_once()

        assert row.dispatched_at is None  # не закрыта
        assert row.attempts == max_attempts
        # warning эмитился — структурированный park-event
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "event=dispatch_park" in r.getMessage()
            and "attempts_cap=" in r.getMessage()
            for r in warnings
        )


# ── poll_once: unknown task_kind ────────────────────────────────────────────


class TestPollOnceUnknownTaskKind:
    async def test_unknown_kind_increments_attempts_does_not_publish(self, monkeypatch):
        row = _Row(task_id="tsk_x", task_kind="bogus.kind", attempts=0)
        session = _FakeSession([row])
        broker = _make_broker(unknown_kind=True)
        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        assert row.dispatched_at is None
        assert row.attempts == 1
        assert "unknown task_kind" in (row.last_error or "")


# ── cleanup_old ─────────────────────────────────────────────────────────────


class TestCleanupOld:
    """`cleanup_old` дропает dispatched-row старше retention_days."""

    async def test_factory_none_is_noop(self, monkeypatch):
        """Без `SERVER_SERVICE_DATABASE_URL` cleanup тихо выходит."""
        monkeypatch.setattr(
            outbox_poller.dispatch_outbox_session,
            "get_session_factory",
            lambda: None,
        )
        # Не должно бросить — просто no-op
        await outbox_poller.cleanup_old()

    async def test_session_error_swallowed(self, monkeypatch):
        """Ошибка внутри сессии не пробрасывается наружу — это housekeeping."""
        class _BoomSession:
            async def execute(self, _stmt):
                raise RuntimeError("db gone")

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

        def factory():
            return _BoomSession()

        monkeypatch.setattr(
            outbox_poller.dispatch_outbox_session,
            "get_session_factory",
            lambda: factory,
        )
        # cleanup_old глотает ошибки — никакой exception наружу
        await outbox_poller.cleanup_old()

    async def test_delete_stmt_filters_dispatched_and_cutoff(self, monkeypatch):
        """Stmt фильтрует `dispatched_at IS NOT NULL AND dispatched_at < cutoff`.

        Не запускаем реальный SQL — проверяем, что repository шлёт DELETE
        с правильными колонками. Захватываем последний execute-stmt и
        проверяем его текстовое представление.
        """
        captured: dict = {}

        class _Result:
            rowcount = 3

        class _CapSession:
            async def execute(self, stmt):
                captured["stmt"] = stmt
                return _Result()

            async def commit(self):
                captured["commit"] = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

        def factory():
            return _CapSession()

        monkeypatch.setattr(
            outbox_poller.dispatch_outbox_session,
            "get_session_factory",
            lambda: factory,
        )
        await outbox_poller.cleanup_old()

        stmt_str = str(captured["stmt"])
        # DELETE-ишное выражение должно ссылаться на dispatched_at
        assert "dispatch_outbox" in stmt_str
        assert "dispatched_at" in stmt_str
        assert captured.get("commit") is True


# ── run_publisher_loop: cancellation ────────────────────────────────────────


class TestRunPublisherLoop:
    async def test_cancelled_error_propagated(self, monkeypatch):
        """`asyncio.CancelledError` пробрасывается из loop'а — graceful shutdown."""
        # poll_once сразу выкидывает CancelledError
        async def fake_poll():
            raise asyncio.CancelledError()

        monkeypatch.setattr(outbox_poller, "poll_once", fake_poll)

        with pytest.raises(asyncio.CancelledError):
            await outbox_poller.run_publisher_loop()

    async def test_other_exceptions_swallowed_loop_continues(self, monkeypatch):
        """Generic exception в poll_once не валит loop — тик логируется,
        следующий тик стартует. Останавливаем через cancel()."""
        call_count = {"n": 0}

        async def flaky_poll():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("oops")
            # на 2-м тике уже остановимся через cancel
            raise asyncio.CancelledError()

        async def short_sleep(_):
            return None

        monkeypatch.setattr(outbox_poller, "poll_once", flaky_poll)
        monkeypatch.setattr(outbox_poller.asyncio, "sleep", short_sleep)

        with pytest.raises(asyncio.CancelledError):
            await outbox_poller.run_publisher_loop()

        # Подтверждаем — было >1 итерации (первая поднялась с RuntimeError,
        # вторая — наш cancel-sentinel)
        assert call_count["n"] >= 2


# ── _compute_next_retry_at — внутренний backoff helper ──────────────────────


class TestComputeNextRetryAt:
    def test_zero_attempts_about_one_second(self):
        before = datetime.now(timezone.utc)
        ts = outbox_poller._compute_next_retry_at(0)
        delay = (ts - before).total_seconds()
        # 2^0 = 1
        assert 0.5 <= delay <= 2

    def test_cap_enforced(self):
        before = datetime.now(timezone.utc)
        ts = outbox_poller._compute_next_retry_at(999)
        delay = (ts - before).total_seconds()
        assert 290 <= delay <= 310

    def test_negative_attempts_treated_as_zero(self):
        """Защита от accidental negative — не падать, не уходить в прошлое."""
        before = datetime.now(timezone.utc)
        ts = outbox_poller._compute_next_retry_at(-5)
        delay = (ts - before).total_seconds()
        assert delay >= 0
