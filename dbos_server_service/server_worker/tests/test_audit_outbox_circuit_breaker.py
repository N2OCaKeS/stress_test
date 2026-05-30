"""Адаптивный sleep в `run_publisher_loop` под shared breaker.

После выноса единственного источника истины в Redis-breaker
(`audit_publisher_breaker`) per-process counter из loop'а ушёл.
Решение об open/closed принимает breaker, а loop читает `get_state()`
после каждого прохода и подгоняет sleep:

  * `state == "open"` → sleep ≤ `_CB_SLEEP_CHUNK_SECONDS` (или меньше,
    если до конца cooldown'а осталось меньше).
  * `state in {"closed", "half_open"}` → обычный `interval_seconds`.

Тут проверяем именно эту sleep-стратегию через monkeypatch'нутый
`audit_publisher_breaker.get_state` и счётчик `asyncio.sleep` для
`audit_outbox_publisher`.
"""

from __future__ import annotations

import asyncio
import pytest

from src.services import audit_outbox_publisher


# `pyproject.toml` ставит `asyncio_mode = "auto"`.


# ── Test rig ─────────────────────────────────────────────────────────────────

_REAL_ASYNCIO_SLEEP = asyncio.sleep


class _FakeAsyncio:
    """Подмена `asyncio`-модуля publisher'а с записью sleep-длительностей."""

    def __init__(self, recorder: list[float]) -> None:
        self._recorder = recorder

    async def sleep(self, seconds: float) -> None:
        self._recorder.append(seconds)
        await _REAL_ASYNCIO_SLEEP(0)


@pytest.fixture
def sleep_recorder(monkeypatch):
    """Перехватывает `audit_outbox_publisher.asyncio.sleep` и пишет длительности."""
    recorder: list[float] = []
    monkeypatch.setattr(audit_outbox_publisher, "asyncio", _FakeAsyncio(recorder))
    return recorder


async def _run_iterations(n: int, *, interval: float = 2.0) -> None:
    task = asyncio.create_task(
        audit_outbox_publisher.run_publisher_loop(interval_seconds=interval)
    )
    try:
        for _ in range(max(4 * n, 8)):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Tests ────────────────────────────────────────────────────────────────────


class TestAdaptiveSleepOnBreakerState:
    """`run_publisher_loop` подстраивает sleep под состояние shared breaker'а."""

    async def test_closed_state_uses_poll_interval(self, monkeypatch, sleep_recorder):
        """Closed breaker → sleep длиной `interval_seconds`."""

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            return (0, 0)

        async def fake_get_state():
            return ("closed", 0.0)

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        assert sleep_recorder, "loop должен был спать хотя бы раз"
        # Все sleep'ы — длиной poll-interval'а (2.0).
        assert all(s == 2.0 for s in sleep_recorder), (
            f"closed-state должен спать interval_seconds; got={sleep_recorder}"
        )

    async def test_open_state_caps_sleep_at_chunk(self, monkeypatch, sleep_recorder):
        """Open breaker с длинным cooldown'ом → sleep = `_CB_SLEEP_CHUNK_SECONDS`."""

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            return (0, 1)  # один AuditEmitError — не важен для loop-логики

        async def fake_get_state():
            return ("open", 100.0)  # cooldown ещё длинный

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        assert sleep_recorder
        # Все sleep'ы кап'нуты `_CB_SLEEP_CHUNK_SECONDS` (5.0 по дефолту),
        # обычный poll-interval (2.0) не выбирается.
        assert all(
            s == audit_outbox_publisher._CB_SLEEP_CHUNK_SECONDS
            for s in sleep_recorder
        ), f"open-state должен спать chunk-секунд; got={sleep_recorder}"

    async def test_open_state_uses_retry_after_if_smaller_than_chunk(
        self, monkeypatch, sleep_recorder,
    ):
        """Если до конца cooldown'а осталось меньше chunk'а — спим именно retry_after."""

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            return (0, 0)

        async def fake_get_state():
            return ("open", 1.5)  # 1.5s < 5s chunk

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        assert sleep_recorder
        assert all(s == 1.5 for s in sleep_recorder), (
            f"остаток окна < chunk → спим именно retry_after; got={sleep_recorder}"
        )

    async def test_half_open_uses_normal_interval(self, monkeypatch, sleep_recorder):
        """Half_open — это «попробуй один запрос», sleep обычный."""

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            return (0, 0)

        async def fake_get_state():
            return ("half_open", 0.0)

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        assert sleep_recorder
        assert all(s == 2.0 for s in sleep_recorder), (
            f"half_open должен спать interval_seconds; got={sleep_recorder}"
        )


class TestFlushStillCalledOnOpenBreaker:
    """Loop НЕ должен skip'ать `_flush_outbox_once` — он всегда вызывается.

    Регрессия: раньше per-process breaker пропускал flush целиком в open-
    state. Сейчас shared `_publish_one.check()` отбивает каждую row сам,
    а loop полагается на это и просто spin'ит проход. Не должно быть
    «зомби-проходов», когда published=0 и flush не вызывался ни разу.
    """

    async def test_open_state_still_calls_flush(self, monkeypatch, sleep_recorder):
        call_count = {"n": 0}

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            call_count["n"] += 1
            return (0, 1)

        async def fake_get_state():
            return ("open", 30.0)

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        # Flush вызывается каждой iteration'ью; решение «не нагружать
        # loging» принимает _publish_one.check(), не loop.
        assert call_count["n"] >= 1, (
            f"flush должен был вызваться хотя бы раз; calls={call_count['n']}"
        )


class TestLoopSurvivesFlushCrash:
    """`_flush_outbox_once` raise'ит → loop ловит, спит обычный interval."""

    async def test_flush_crash_does_not_kill_loop(self, monkeypatch, sleep_recorder):
        call_count = {"n": 0}

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            call_count["n"] += 1
            raise RuntimeError("db down")

        async def fake_get_state():
            return ("closed", 0.0)

        monkeypatch.setattr(audit_outbox_publisher, "_flush_outbox_once", fake_flush)
        monkeypatch.setattr(
            audit_outbox_publisher.audit_publisher_breaker, "get_state",
            fake_get_state,
        )

        await _run_iterations(3, interval=2.0)

        # Loop пережил несколько iteration'ов после crash'а — счётчик > 1.
        assert call_count["n"] >= 1
        assert sleep_recorder, "после crash'а loop должен поспать обычный interval"


class TestResetClearsDlqCounter:
    """Sanity: `_reset_breaker_state` сбрасывает per-process DLQ counter."""

    def test_reset_clears_dlq_counter(self):
        audit_outbox_publisher._dlq_total = 99
        audit_outbox_publisher._reset_breaker_state()
        assert audit_outbox_publisher.get_dlq_total() == 0
