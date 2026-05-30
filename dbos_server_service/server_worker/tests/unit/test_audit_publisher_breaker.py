"""Unit tests for shared audit-publisher circuit breaker.

Замокан Redis-клиент: вместо `redis.asyncio.from_url` подсовываем
`FakeRedis` с in-memory dict'ом + py-репликой Lua-скриптов. Проверяется
state-machine: closed→open, open-rejects, half_open recovery, шаринг
state между «репликами» и fail-open на ошибках Redis.

В отличие от bmc-breaker'а тут нет аргумента `host` — канал один на
весь worker (publisher → loging_service), keys фиксированные.
"""

from __future__ import annotations

import pytest

from src.services import audit_publisher_breaker as cb


# ── Минимальный fake Redis для Lua-скриптов breaker'а ────────────────────────


class FakeRedis:
    """In-memory Redis с py-реализацией трёх Lua-скриптов breaker'а.

    `_store` — class-level: все инстансы шарят один dict, что моделирует
    «N реплик worker'а смотрят в один Redis». TTL не симулируем — тестам
    хватает явного advance через `frozen_clock`.
    """

    _store: dict[str, str] = {}

    def __init__(self) -> None:
        pass

    @classmethod
    def reset_store(cls) -> None:
        cls._store.clear()

    async def eval(self, script: str, n: int, *args: str):  # noqa: PLR0911
        keys = list(args[:n])
        argv = list(args[n:])

        if script == cb._CHECK_SCRIPT:
            now = int(argv[0])
            cooldown = int(argv[1])
            state = self._store.get(keys[1])
            open_until_raw = self._store.get(keys[2], "0")
            try:
                open_until = int(open_until_raw)
            except ValueError:
                open_until = 0
            if state == "open":
                if open_until > now:
                    return [state, open_until - now]
                self._store[keys[1]] = "half_open"
                self._store.pop(keys[2], None)
                return ["half_open", 0]
            if state is not None:
                return [state, 0]
            return ["closed", 0]

        if script == cb._RECORD_SUCCESS_SCRIPT:
            for k in keys:
                self._store.pop(k, None)
            return 1

        if script == cb._RECORD_FAILURE_SCRIPT:
            now = int(argv[0])
            threshold = int(argv[1])
            cooldown = int(argv[3])
            try:
                cur = int(self._store.get(keys[0], "0"))
            except ValueError:
                cur = 0
            cur += 1
            self._store[keys[0]] = str(cur)
            if cur >= threshold:
                self._store[keys[1]] = "open"
                self._store[keys[2]] = str(now + cooldown)
                self._store.pop(keys[0], None)
                return ["open", cur]
            return ["closed", cur]

        raise AssertionError(f"unexpected script: {script[:40]!r}")

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._store:
                self._store.pop(k, None)
                n += 1
        return n

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    """Подменяет `_get_client` в audit_publisher_breaker на FakeRedis-фабрику."""

    FakeRedis.reset_store()

    async def fake_get_client():
        return FakeRedis()

    monkeypatch.setattr(cb, "_get_client", fake_get_client)
    return FakeRedis


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Контроль `time.time()` внутри breaker'а для проверок cooldown'а."""
    state = {"now": 1_700_000_000.0}

    def fake_time() -> float:
        return state["now"]

    monkeypatch.setattr(cb.time, "time", fake_time)
    return state


# ── Тесты ────────────────────────────────────────────────────────────────────


class TestClosedToOpen:
    """Threshold failure'ов закрытого breaker'а открывает его."""

    async def test_closed_state_check_passes(self, fake_redis, frozen_clock) -> None:
        # Чистый Redis → state="closed" → check не бросает.
        await cb.check()

    async def test_failures_at_threshold_open_circuit(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        with pytest.raises(cb.CircuitBreakerOpenError) as ei:
            await cb.check()
        # error_code зафиксирован — SIEM/UI ловят по нему.
        assert ei.value.error_code == "AUDIT_PUBLISHER_CIRCUIT_OPEN"
        retry_after = ei.value.details["retry_after_seconds"]
        assert 0 < retry_after <= cb.DEFAULT_COOLDOWN_SECONDS


class TestOpenRejects:
    """Open breaker отбивает запросы пока не истечёт cooldown."""

    async def test_open_keeps_rejecting_during_cooldown(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        # Несколько проверок подряд внутри open-окна — все отбиваются.
        for _ in range(3):
            with pytest.raises(cb.CircuitBreakerOpenError):
                await cb.check()


class TestHalfOpenRecovery:
    """После cooldown'а breaker уходит в half_open; success → closed."""

    async def test_half_open_success_closes_breaker(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        # Прыгнули за cooldown.
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1
        await cb.check()  # half_open — пропускает пробный запрос
        # Симулируем успех пробного запроса.
        await cb.record_success()
        # Все ключи снесены — следующий check пройдёт как closed.
        failures_key, state_key, open_until_key = cb._keys()
        assert state_key not in fake_redis._store
        assert open_until_key not in fake_redis._store
        assert failures_key not in fake_redis._store
        await cb.check()


class TestMultiReplicaSharing:
    """State виден между «репликами» (FakeRedis._store шарится)."""

    async def test_failures_accumulate_across_replicas(
        self, fake_redis, frozen_clock,
    ) -> None:
        # «Реплика A» накапливает половину threshold'а.
        half = cb.DEFAULT_FAILURE_THRESHOLD // 2
        for _ in range(half):
            await cb.record_failure()
        # «Реплика B» (новый клиент, тот же store) добивает до threshold.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD - half):
            await cb.record_failure()
        # Любая реплика теперь видит open — это и есть весь смысл
        # shared state'а (с per-process breaker'ом было бы N×threshold).
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()


class TestRedisFailureFailOpen:
    """Если Redis недоступен — breaker не блокирует POST (fail-open)."""

    async def test_check_swallows_redis_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BrokenRedis:
            async def eval(self, *a, **kw):
                raise ConnectionError("redis down")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BrokenRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        # Не должен бросать — failed Redis приравнивается к closed-state.
        await cb.check()

    async def test_record_failure_swallows_redis_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BrokenRedis:
            async def eval(self, *a, **kw):
                raise ConnectionError("redis down")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BrokenRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        # record_failure при упавшем Redis не должен ронять publisher.
        await cb.record_failure()
