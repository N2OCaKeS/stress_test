"""Unit-тесты: `_ensure_broker_started` — защита от race в одном event loop.

Закрывает баг «`_ensure_broker_started` race внутри одного процесса (нет
asyncio.Lock)». До фикса: два concurrent `dispatch_task` могли
оба пройти проверку `if not _broker_started:` ДО того, как первый успеет
выставить флаг — оба вызвали бы `broker.startup()` (второй startup может
упасть или привести к inconsistent state в taskiq-redis; идемпотентность —
implementation detail библиотеки, не контракт).

Фикс: `_broker_lock = asyncio.Lock()` на module-level + double-check pattern
внутри `_ensure_broker_started` (fast path без lock'а если уже стартован,
slow path с lock'ом + повторный check).

Тесты:
- happy path: 10 параллельных `_ensure_broker_started` → `broker.startup()`
  вызван **ровно 1 раз** (mock-counter).
- последовательные вызовы тоже идемпотентны: startup → done → startup → no-op.
- если первый startup упал (Redis down) — флаг не выставлен, следующий вызов
  попробует снова (lock не «залипает» в открытом состоянии).
- `_build_broker` неконфигурированной (нет URL) raise'ится ДО lock'а — это
  важно для случая «redis URL пуст»: missing-config не должна сериализоваться.
"""
from __future__ import annotations

import asyncio

import pytest

from src.core.exceptions import ServiceUnavailableError
from src.services import worker_client


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeBroker:
    """Имитирует taskiq broker: считает вызовы `.startup()`, опционально raise."""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.startup_calls = 0
        self._raise_exc = raise_exc

    async def startup(self) -> None:
        self.startup_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc


@pytest.fixture
def reset_broker_state(monkeypatch):
    """Сбрасывает `_broker_started` и `_worker_broker` перед каждым тестом.

    Также пересоздаёт `_broker_lock` свежий `asyncio.Lock()` — иначе lock,
    привязанный к предыдущему event loop, сломает тесты под `asyncio_mode=auto`,
    где каждый тест получает свой loop.
    """
    monkeypatch.setattr(worker_client, "_broker_started", False)
    monkeypatch.setattr(worker_client, "_worker_broker", None)
    monkeypatch.setattr(worker_client, "_broker_lock", asyncio.Lock())
    yield


@pytest.fixture
def install_fake_broker(monkeypatch, reset_broker_state):
    """Подменяет `_build_broker` чтобы возвращал переданный fake-broker.

    Использование: `broker = install_fake_broker(_FakeBroker())` →
    `_ensure_broker_started` будет использовать наш fake.
    """

    def _install(broker: _FakeBroker) -> _FakeBroker:
        def _fake_build_broker():
            return broker

        monkeypatch.setattr(worker_client, "_build_broker", _fake_build_broker)
        return broker

    return _install


# ── Главный тест: 10 параллельных вызовов → startup ровно 1 раз ──────────────


class TestEnsureBrokerStartedRaceProtection:
    """`asyncio.Lock` гарантирует ровно один `broker.startup()` под gather."""

    async def test_ten_parallel_calls_startup_called_once(self, install_fake_broker):
        """10 параллельных `_ensure_broker_started` через `asyncio.gather` →
        `broker.startup()` вызван **ровно 1 раз**, остальные 9 ждут lock
        и видят `_broker_started=True` после double-check.
        """
        broker = install_fake_broker(_FakeBroker())

        await asyncio.gather(
            *(worker_client._ensure_broker_started() for _ in range(10))
        )

        assert broker.startup_calls == 1, (
            f"broker.startup() должен быть вызван ровно один раз, "
            f"получено {broker.startup_calls}"
        )
        assert worker_client._broker_started is True

    async def test_many_parallel_calls_with_slow_startup(self, install_fake_broker, monkeypatch):
        """startup() искусственно медленный (sleep) → за время первого
        startup'а все 20 параллельных вызовов успевают войти в `_ensure_broker_started`
        и ждать на lock'е. Без lock'а — N startup'ов; с lock'ом — ровно 1.
        """

        class _SlowBroker:
            def __init__(self) -> None:
                self.startup_calls = 0

            async def startup(self) -> None:
                # Даём event loop'у время прокрутить все 20 корутин до lock'а
                await asyncio.sleep(0.01)
                self.startup_calls += 1

        broker = _SlowBroker()
        monkeypatch.setattr(worker_client, "_build_broker", lambda: broker)

        await asyncio.gather(
            *(worker_client._ensure_broker_started() for _ in range(20))
        )

        assert broker.startup_calls == 1

    async def test_sequential_calls_startup_called_once(self, install_fake_broker):
        """Idempotency после успешного startup'а: второй и последующие вызовы
        — no-op (fast path: первый check видит `_broker_started=True` без lock'а).
        """
        broker = install_fake_broker(_FakeBroker())

        await worker_client._ensure_broker_started()
        await worker_client._ensure_broker_started()
        await worker_client._ensure_broker_started()

        assert broker.startup_calls == 1


# ── Failure modes: lock не «залипает», флаг не выставляется при ошибке ───────


class TestEnsureBrokerStartedFailureBehavior:
    """Если `broker.startup()` упал — `_broker_started` остаётся `False`,
    lock освобождается, следующий вызов попробует снова."""

    async def test_startup_failure_does_not_set_flag(self, install_fake_broker):
        """`broker.startup()` raise → `_broker_started` остался False."""
        broker = install_fake_broker(
            _FakeBroker(raise_exc=ConnectionError("redis down"))
        )

        with pytest.raises(ConnectionError):
            await worker_client._ensure_broker_started()

        assert worker_client._broker_started is False
        # startup() реально дёрнулся ровно один раз
        assert broker.startup_calls == 1

    async def test_retry_after_startup_failure_calls_startup_again(self, install_fake_broker, monkeypatch):
        """Первый вызов: startup() упал (Redis down). Второй вызов: startup
        должен попробовать снова (не быть закэшированным `_broker_started=True`).
        """
        # Первый broker — падает
        failing_broker = _FakeBroker(raise_exc=ConnectionError("redis flaky"))
        monkeypatch.setattr(worker_client, "_build_broker", lambda: failing_broker)

        with pytest.raises(ConnectionError):
            await worker_client._ensure_broker_started()

        # Подменяем на «работающий» broker — имитируем что Redis вернулся
        ok_broker = _FakeBroker()
        monkeypatch.setattr(worker_client, "_build_broker", lambda: ok_broker)

        # Второй вызов должен пройти и вызвать startup() на новом broker'е
        await worker_client._ensure_broker_started()

        assert ok_broker.startup_calls == 1
        assert worker_client._broker_started is True

    async def test_parallel_calls_with_failing_startup(self, install_fake_broker):
        """10 параллельных вызовов, startup() raise → один из них поднимает
        исключение (тот, что схватил lock первым), флаг остался False.
        Дальше остальные ждали lock'а и тоже поднимут или сделают свой startup,
        но `_broker_started` НЕ должен оказаться True.
        """
        broker = install_fake_broker(
            _FakeBroker(raise_exc=ConnectionError("redis down"))
        )

        results = await asyncio.gather(
            *(worker_client._ensure_broker_started() for _ in range(10)),
            return_exceptions=True,
        )

        # Все 10 должны были упасть (либо первый startup'ом, либо следующие
        # после re-take lock'а с тем же failing-broker'ом)
        assert all(isinstance(r, ConnectionError) for r in results), (
            f"Все 10 вызовов должны raise ConnectionError, получено: {results}"
        )
        assert worker_client._broker_started is False
        # startup() мог быть вызван от 1 до 10 раз (после первого fail остальные
        # видят `_broker_started=False` и пробуют снова), но никогда не выставит флаг
        assert broker.startup_calls >= 1


# ── `_build_broker` raise ДО lock'а: missing-config не сериализуется ─────────


class TestEnsureBrokerStartedBuildBrokerFailure:
    """`_build_broker` поднимает `ServiceUnavailableError(WORKER_REDIS_NOT_CONFIGURED)`
    когда нет URL. Эта ошибка не должна попадать под lock — она настолько ранняя,
    что lock на ней бессмысленен (и сериализация missing-config raise'ов
    замедлила бы N конкурентных запросов).
    """

    async def test_build_broker_raise_does_not_touch_flag(self, monkeypatch, reset_broker_state):
        """`_build_broker` raise → флаг остаётся False, ничего больше не происходит."""

        def boom_build():
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="SERVER_WORKER_REDIS_URL is not set",
            )

        monkeypatch.setattr(worker_client, "_build_broker", boom_build)

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await worker_client._ensure_broker_started()

        assert exc_info.value.error_code == "WORKER_REDIS_NOT_CONFIGURED"
        assert worker_client._broker_started is False

    async def test_parallel_build_broker_failures(self, monkeypatch, reset_broker_state):
        """10 параллельных вызовов при отсутствии Redis URL → все 10 получают
        `WORKER_REDIS_NOT_CONFIGURED` (быстро, без сериализации на lock'е).
        """

        def boom_build():
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="SERVER_WORKER_REDIS_URL is not set",
            )

        monkeypatch.setattr(worker_client, "_build_broker", boom_build)

        results = await asyncio.gather(
            *(worker_client._ensure_broker_started() for _ in range(10)),
            return_exceptions=True,
        )

        assert len(results) == 10
        for r in results:
            assert isinstance(r, ServiceUnavailableError)
            assert r.error_code == "WORKER_REDIS_NOT_CONFIGURED"

        assert worker_client._broker_started is False


# ── Double-check pattern: вход в lock после уже-стартованного флага ──────────


class TestDoubleCheckPattern:
    """Sanity check для double-check pattern: если поток А успел поднять lock,
    выполнить startup, выставить флаг и отпустить lock — поток Б, который
    ждал lock, должен НЕ вызвать startup повторно (второй check внутри lock'а).
    """

    async def test_second_waiter_sees_flag_inside_lock(self, monkeypatch, reset_broker_state):
        """Сложный сценарий: используем `asyncio.Event` чтобы синхронизировать
        две корутины. Первая держит lock и выполняет startup. Вторая ждёт.
        После того как первая отпустит lock, вторая войдёт — должна увидеть
        `_broker_started=True` через double-check и **не** вызвать startup.
        """
        startup_started = asyncio.Event()
        release_first = asyncio.Event()
        call_count = 0

        class _SyncedBroker:
            async def startup(self):
                nonlocal call_count
                call_count += 1
                startup_started.set()
                await release_first.wait()

        broker = _SyncedBroker()
        monkeypatch.setattr(worker_client, "_build_broker", lambda: broker)

        # Запускаем первую — она войдёт в lock + startup() + застрянет на release_first
        first_task = asyncio.create_task(worker_client._ensure_broker_started())
        # Ждём пока startup начнётся (значит, первая внутри lock'а)
        await startup_started.wait()

        # Запускаем вторую — она войдёт в `_ensure_broker_started`, увидит флаг
        # `_broker_started=False`, попытается взять lock — и заблокируется
        second_task = asyncio.create_task(worker_client._ensure_broker_started())
        # Даём scheduler'у запустить вторую корутину до момента блокировки на lock'е.
        # `lock.locked()` становится True когда кто-то держит lock + есть waiter'ы.
        # Для надёжности крутимся в цикле пока в lock'е реально стоит waiter.
        for _ in range(100):
            await asyncio.sleep(0)
            # `_broker_lock._waiters` — internal, но проверка через .locked()
            # подойдёт: lock locked первой корутиной, второй ждёт acquire
            if worker_client._broker_lock.locked() and second_task is not None:
                # Дополнительная пауза чтобы убедиться что second реально вошла в acquire
                await asyncio.sleep(0)
                break

        # Освобождаем первую — она выставит флаг + отпустит lock; вторая войдёт
        release_first.set()

        await asyncio.gather(first_task, second_task)

        # startup() должен был быть вызван ровно один раз (вторая увидела флаг
        # через double-check внутри lock'а)
        assert call_count == 1
        assert worker_client._broker_started is True
