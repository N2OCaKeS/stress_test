"""Circuit breaker для `run_publisher_loop`.

Регрессия: без breaker'а worker циклом ретраит `flush_outbox`, и если
loging_service ляжет, нагружает
его ещё больше. После `_CB_FAILURE_THRESHOLD` (=5) подряд iteration'ов
с `AuditEmitError` breaker открывается на exponential back-off
(`2 ** failures`, capped 5 min); пока open — `_flush_outbox_once` не
зовётся вовсе. Успешный iteration (или пустой outbox) сбрасывает
счётчик.

Тестируем именно `run_publisher_loop`, а не `flush_outbox` напрямую —
breaker-state живёт на module-level и активизируется только в
background-loop'е. Inline-вызов из `_runner._safe_flush_outbox()`
breaker'у не подчиняется (см. docstring `flush_outbox`).

Стратегия:
  * Запускаем `run_publisher_loop` как asyncio.Task,
    monkeypatch'им `time.monotonic` (детерминизм) и
    `asyncio.sleep` (мгновенное прохождение времени),
  * через несколько iteration'ов читаем module-level state и
    счётчик вызовов `_flush_outbox_once`,
  * cancel'им Task в finally.

`asyncio.sleep` подменяется на no-op, который продвигает виртуальное
время через shared `clock` — иначе тесты висели бы по реальным
секундам.
"""

from __future__ import annotations

import asyncio
import pytest

from src.services import audit_outbox_publisher


# `pyproject.toml` ставит `asyncio_mode = "auto"` — async-функции
# автоматически становятся asyncio-тестами.


# ── Test rig ─────────────────────────────────────────────────────────────────


# Сохраняем «настоящий» asyncio.sleep ДО монки-патчей. Иначе
# `_FakeClock.sleep`, который сам зовёт `asyncio.sleep(0)`, попадёт в
# себя же → infinite recursion. Используем raw-ссылку из модуля стандарт-
# либы напрямую.
_REAL_ASYNCIO_SLEEP = asyncio.sleep


class _FakeClock:
    """Монотонные часы под контролем теста.

    `time.monotonic()` → `now`, `asyncio.sleep(s)` → продвинуть `now` на
    `s` и yield-нуть управление через `_REAL_ASYNCIO_SLEEP(0)` — даёт
    другим корутинам шанс выполниться, не съедает реального времени и
    не рекурсит в самого себя.
    """

    def __init__(self) -> None:
        self.now: float = 1000.0  # старт с произвольной точки, не с 0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        # Даём loop'у шанс перейти к другим задачам — без этого
        # `run_publisher_loop` крутился бы в одной корутине без point'а
        # для cancel'а.
        await _REAL_ASYNCIO_SLEEP(0)


class _FakeAsyncio:
    """Минимальная подмена `asyncio`-модуля для publisher'а.

    Publisher использует только `asyncio.sleep` — подменяем именно
    его, остальные атрибуты публишеру не нужны.
    """

    def __init__(self, sleep_fn):
        self.sleep = sleep_fn


class _FakeTime:
    """Минимальная подмена `time`-модуля для publisher'а (только
    `monotonic`)."""

    def __init__(self, monotonic_fn):
        self.monotonic = monotonic_fn


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    # Патчим module-level attribute publisher'а (`audit_outbox_publisher.time`,
    # `audit_outbox_publisher.asyncio`) — заменяем сами ссылки на наши
    # stub'ы. Так monkeypatch на teardown'е восстановит оригиналы и не
    # затронет глобальные `time`/`asyncio` (что сломало бы pytest_asyncio
    # и другие тесты в том же процессе).
    monkeypatch.setattr(audit_outbox_publisher, "time", _FakeTime(clock.monotonic))
    monkeypatch.setattr(
        audit_outbox_publisher, "asyncio", _FakeAsyncio(clock.sleep),
    )
    return clock


@pytest.fixture(autouse=True)
def reset_breaker():
    """Изоляция: breaker-state — module-level, общий для процесса."""
    audit_outbox_publisher._reset_breaker_state()
    yield
    audit_outbox_publisher._reset_breaker_state()


async def _run_iterations(n: int, *, interval: float = 2.0) -> None:
    """Запустить `run_publisher_loop` и дать ему сделать ~`n` iteration'ов.

    Под `_FakeClock.sleep` (no-op по реальному времени) — это
    эквивалентно `await asyncio.sleep(0)` * (несколько раз). Чтобы
    дать loop'у пройти ровно n iteration'ов, мы используем счётчик
    вызовов через monkeypatch'нутый `_flush_outbox_once` либо
    barrier-based подход. Здесь — простой подход: запускаем loop как
    Task, делаем 4*n `await asyncio.sleep(0)` (учитывает и
    `_flush_outbox_once`-await, и `await asyncio.sleep(interval)`), и
    cancel'им.
    """
    task = asyncio.create_task(
        audit_outbox_publisher.run_publisher_loop(interval_seconds=interval)
    )
    try:
        # Каждая iteration loop'а имеет ~2 await-точки: _flush_outbox_once
        # (или sleep на open-breaker) и финальный sleep(interval_seconds).
        # 4*n yields — с запасом, чтобы все n iteration'ов прокрутились.
        for _ in range(max(4 * n, 8)):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Tests ────────────────────────────────────────────────────────────────────


class TestBreakerOpensAfterConsecutiveFailures:
    """5 подряд AuditEmitError → breaker open → следующий iteration не
    зовёт `_flush_outbox_once`."""

    async def test_five_consecutive_audit_emit_errors_open_breaker(
        self, monkeypatch, fake_clock,
    ):
        call_count = {"n": 0}

        async def fake_flush_audit_fail(*, limit=audit_outbox_publisher._BATCH_SIZE):
            # Имитируем iteration с одним AuditEmitError'ом.
            call_count["n"] += 1
            return (0, 1)  # published=0, audit_emit_errors=1

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush_audit_fail,
        )

        # Прогоняем минимум 5 iteration'ов — counter должен дойти до 5
        # и breaker открыться.
        await _run_iterations(6)

        # Breaker открыт: счётчик дошёл до threshold, окно установлено.
        assert audit_outbox_publisher._consecutive_failures >= 5
        # Окно было выставлено в какой-то момент; даже если fake_clock
        # ушёл вперёд за время теста — само значение должно быть
        # положительным (а не 0.0, как в closed state).
        assert audit_outbox_publisher._circuit_open_until > 0.0, (
            "breaker должен был открыться → _circuit_open_until != 0.0"
        )

    async def test_open_breaker_skips_flush(self, monkeypatch, fake_clock):
        """Пока breaker open — `_flush_outbox_once` не вызывается."""
        call_count = {"n": 0}

        async def fake_flush_audit_fail(*, limit=audit_outbox_publisher._BATCH_SIZE):
            call_count["n"] += 1
            return (0, 1)

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush_audit_fail,
        )

        # Принудительно открываем breaker на «далеко вперёд» — чтобы
        # iteration'ы 100% попадали в open-window.
        audit_outbox_publisher._consecutive_failures = 5
        audit_outbox_publisher._circuit_open_until = fake_clock.now + 1000.0

        await _run_iterations(10)

        # `_flush_outbox_once` не вызвался ни разу — все iteration'ы
        # ушли в continue по open-проверке.
        assert call_count["n"] == 0, (
            f"open breaker не должен вызывать flush; calls={call_count['n']}"
        )


class TestBreakerClosesAfterCooldown:
    """После `_circuit_open_until` breaker закрывается → emit пытается
    снова."""

    async def test_breaker_closes_when_window_expires(self, monkeypatch, fake_clock):
        flush_calls = []

        async def fake_flush_success(*, limit=audit_outbox_publisher._BATCH_SIZE):
            flush_calls.append(fake_clock.now)
            # Успешный iteration — пустой outbox или всё опубликовано,
            # audit_emit_errors=0.
            return (0, 0)

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush_success,
        )

        # Открываем breaker на короткое окно — после прохождения времени
        # loop должен снова вызвать flush.
        open_window = 10.0
        audit_outbox_publisher._consecutive_failures = 5
        audit_outbox_publisher._circuit_open_until = fake_clock.now + open_window

        # Прогон: пока `now < open_until` — flush не зовётся.
        # `_FakeClock.sleep` ускоренное виртуальное время; кол-во
        # iteration'ов loop'а > open_window / _CB_SLEEP_CHUNK_SECONDS.
        await _run_iterations(15)

        # Хоть один вызов flush должен случиться после того, как окно
        # истекло.
        assert len(flush_calls) >= 1, (
            "после истечения окна breaker должен закрыться и вызвать flush"
        )
        # И все вызовы — после открытого окна.
        assert all(t >= 1000.0 + open_window for t in flush_calls), (
            f"flush до истечения окна: calls={flush_calls}, "
            f"window_end={1000.0 + open_window}"
        )

        # После успешного flush'а breaker сброшен.
        assert audit_outbox_publisher._consecutive_failures == 0
        assert audit_outbox_publisher._circuit_open_until == 0.0


class TestBreakerResetsOnSuccess:
    """Success после первого fail → счётчик сбрасывается, breaker не
    открывается."""

    async def test_single_failure_then_success_resets_counter(
        self, monkeypatch, fake_clock,
    ):
        results = iter([
            (0, 1),  # 1-я iteration: один AuditEmitError → failures=1
            (1, 0),  # 2-я iteration: успех → failures=0
            (1, 0),
            (1, 0),
            (1, 0),
            (1, 0),  # ещё несколько успешных
        ])

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            try:
                return next(results)
            except StopIteration:
                # После того как сценарий проигран — возвращаем «пусто»,
                # чтобы loop спокойно крутился до cancel'а.
                return (0, 0)

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush,
        )

        await _run_iterations(8)

        # Breaker никогда не открывался: ни одной серии из 5.
        assert audit_outbox_publisher._consecutive_failures == 0
        assert audit_outbox_publisher._circuit_open_until == 0.0

    async def test_empty_outbox_resets_failure_counter(
        self, monkeypatch, fake_clock,
    ):
        """Пустой outbox = успешный iteration; даже после нескольких
        fail'ов одно «пусто» сбрасывает счётчик."""
        # 3 fail'а подряд (не дотягивает до threshold=5), потом «пусто».
        results = iter([
            (0, 1),
            (0, 1),
            (0, 1),
            (0, 0),  # outbox empty — reset
            (0, 0),
        ])

        async def fake_flush(*, limit=audit_outbox_publisher._BATCH_SIZE):
            try:
                return next(results)
            except StopIteration:
                return (0, 0)

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush,
        )

        await _run_iterations(8)

        assert audit_outbox_publisher._consecutive_failures == 0


class TestBreakerIgnoresNonAuditEmitErrors:
    """Программные ошибки (сериализация и т.п.) breaker НЕ открывают —
    у них audit_emit_errors=0 даже если published=0."""

    async def test_programming_errors_do_not_open_breaker(
        self, monkeypatch, fake_clock,
    ):
        async def fake_flush_programming_error(
            *, limit=audit_outbox_publisher._BATCH_SIZE,
        ):
            # published=0, audit_emit_errors=0 — означает «была программная
            # ошибка, не HTTP/transport» (или просто пусто).
            return (0, 0)

        monkeypatch.setattr(
            audit_outbox_publisher,
            "_flush_outbox_once",
            fake_flush_programming_error,
        )

        # 20 iteration'ов с «программной ошибкой» — breaker остаётся
        # закрытым (failures всегда сбрасываются в 0).
        await _run_iterations(20)

        assert audit_outbox_publisher._consecutive_failures == 0
        assert audit_outbox_publisher._circuit_open_until == 0.0


class TestBreakerBackOffCappedAtMax:
    """Exponential back-off не уезжает выше `_CB_MAX_OPEN_SECONDS`."""

    async def test_back_off_capped_at_5_minutes(self, monkeypatch, fake_clock):
        """При большом числе подряд fail'ов окно не превышает
        `_CB_MAX_OPEN_SECONDS` (=300s)."""

        async def fake_flush_audit_fail(*, limit=audit_outbox_publisher._BATCH_SIZE):
            return (0, 1)

        monkeypatch.setattr(
            audit_outbox_publisher, "_flush_outbox_once", fake_flush_audit_fail,
        )

        # Симулируем «очень много» fail'ов: 20 подряд → 2**20 = 1M секунд,
        # но cap = 300.
        audit_outbox_publisher._consecutive_failures = 19
        # Дальше один iteration loop'а добавит ещё один fail и пересчитает
        # окно.
        # Принудительно «закрываем» окно, чтобы loop вошёл в flush.
        audit_outbox_publisher._circuit_open_until = 0.0

        # Запускаем как Task на один-два iteration'а — достаточно, чтобы
        # обновить failure-счётчик и пересчитать open_until.
        task = asyncio.create_task(
            audit_outbox_publisher.run_publisher_loop(interval_seconds=2.0)
        )
        try:
            # Дать loop'у успеть одну iteration после flush'а.
            for _ in range(6):
                await asyncio.sleep(0)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 2 ** 20 = 1_048_576 >> 300 → должно cap'нуться.
        open_for = (
            audit_outbox_publisher._circuit_open_until - 1000.0  # старт fake_clock
        )
        # `_FakeClock` может уже продвинуться от sleep'ов внутри loop'а,
        # поэтому проверяем длину окна относительно момента, когда
        # _circuit_open_until был назначен. Грубая проверка:
        # back_off <= _CB_MAX_OPEN_SECONDS + (минимальный сдвиг от sleep'ов).
        assert audit_outbox_publisher._consecutive_failures >= 20
        # Reasonable upper bound: cap + допустимый дрейф «фейк-часов».
        assert open_for <= audit_outbox_publisher._CB_MAX_OPEN_SECONDS + 60, (
            f"back_off не должен превышать cap; open_until - start = {open_for}"
        )


class TestBreakerStateIsModuleLevel:
    """Sanity: `_reset_breaker_state` действительно сбрасывает то, что
    нужно — гарантия для других тестов, которые на это полагаются."""

    def test_reset_clears_failures_and_open_until(self):
        audit_outbox_publisher._consecutive_failures = 99
        audit_outbox_publisher._circuit_open_until = 1e9
        audit_outbox_publisher._reset_breaker_state()
        assert audit_outbox_publisher._consecutive_failures == 0
        assert audit_outbox_publisher._circuit_open_until == 0.0
