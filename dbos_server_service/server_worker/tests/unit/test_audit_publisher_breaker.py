"""Unit tests for shared audit-publisher circuit breaker.

Замокан Redis-клиент: общий FakeRedis из `_breaker_test_helpers`
(те же три Lua-скрипта живут в `_breaker_lua`). Проверяется
state-machine: closed→open, open-rejects, half_open recovery, шаринг
state между «репликами» и fail-open на ошибках Redis.

В отличие от bmc-breaker'а тут нет аргумента `host` — канал один на
весь worker (publisher → loging_service), keys фиксированные.
"""

from __future__ import annotations

import pytest

from src.services import audit_publisher_breaker as cb
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, cb)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, cb)


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

    async def test_record_success_swallows_redis_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`record_success` после 2xx должен пережить падение Redis.

        Симметрия `record_failure`: если у нас отвалился Redis ровно в
        момент успешного публиша, мы не хотим вернуть исключение в
        `_publish_one` и оставить outbox-row unpublished. Лучше log
        WARNING и забыть про обновление breaker'а — следующий проход
        сам обнаружит свежее состояние.
        """

        class BrokenRedis:
            async def eval(self, *a, **kw):
                raise ConnectionError("redis down")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BrokenRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        # Не должен бросать.
        await cb.record_success()


class TestReset:
    """Operator/test helper: `reset()` сбрасывает все три ключа."""

    async def test_breaker_reset_clears_state(self, fake_redis, frozen_clock) -> None:
        """После `reset()` breaker возвращается в closed: counter, state,
        open_until — все снесены."""
        # Доводим до open.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()

        # Operator-вмешательство: reset.
        await cb.reset()

        # Все три ключа снесены.
        failures_key, state_key, open_until_key = cb._keys()
        assert failures_key not in fake_redis._store
        assert state_key not in fake_redis._store
        assert open_until_key not in fake_redis._store
        # И check после reset — closed, не бросает.
        await cb.check()


class TestNonTransportException:
    """Не-транспортные exception'ы из Lua/Redis пробрасываются наверх.

    Симметрично BMC-breaker'у: `RedisError`/`OSError`/`TimeoutError`
    дают fail-open, всё прочее (RuntimeError, KeyError, AttributeError)
    — программный баг, который не должен прятаться за «канал прошёл».
    """

    async def test_check_propagates_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BadRedis:
            async def eval(self, *a, **kw):
                raise RuntimeError("lua script bug")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BadRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        with pytest.raises(RuntimeError, match="lua script bug"):
            await cb.check()

    async def test_get_state_propagates_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BadRedis:
            async def eval(self, *a, **kw):
                raise RuntimeError("get_state bug")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BadRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        with pytest.raises(RuntimeError, match="get_state bug"):
            await cb.get_state()

    async def test_record_failure_propagates_key_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BadRedis:
            async def eval(self, *a, **kw):
                raise KeyError("missing key")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BadRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        with pytest.raises(KeyError):
            await cb.record_failure()

    async def test_record_success_propagates_attribute_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BadRedis:
            async def eval(self, *a, **kw):
                raise AttributeError("borked")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BadRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        with pytest.raises(AttributeError):
            await cb.record_success()

    async def test_reset_propagates_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BadRedis:
            async def delete(self, *a, **kw):
                raise RuntimeError("delete bug")

            async def aclose(self):
                return None

        async def fake_get_client():
            return BadRedis()

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        with pytest.raises(RuntimeError, match="delete bug"):
            await cb.reset()
