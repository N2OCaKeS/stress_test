"""Unit tests for shared BMC circuit breaker.

Замокан Redis-клиент: вместо реального ``redis.asyncio.from_url`` подсовываем
``FakeRedis`` с in-memory dict'ом + py-репликой Lua-скриптов. Тест проверяет
само поведение state-machine: closed→open, open-rejects, half_open→closed
после success, и шарится ли state между несколькими «репликами» (двумя
вызовами модуля поверх одного и того же dict'а).
"""

from __future__ import annotations

import pytest

from src.services import bmc_circuit_breaker as cb


# ── Минимальный fake Redis для Lua-скриптов breaker'а ────────────────────────
#
# Из всего API реального ``redis.asyncio.Redis`` breaker использует:
#   * ``eval(script, n, *keys_and_args)`` — выполнить Lua-скрипт;
#   * ``delete(*keys)`` — снести ключи (используется в ``reset``);
#   * ``aclose()`` — закрыть клиент.
# Этого достаточно. Скрипты у нас три — реализуем их семантически на Python.


class FakeRedis:
    """In-memory Redis с поддержкой нужных Lua-скриптов breaker'а.

    Один store расшарен между всеми инстансами — это и моделирует поведение
    «двух реплик worker'а смотрят на один Redis».

    TTL не симулируем штатно: для тестов хватает явного advance_time через
    ``now_provider`` (его использует breaker, когда мы monkeypatch'им
    ``time.time``). Это ОК, потому что breaker сам не делает PTTL/EXISTS —
    он сравнивает ``open_until`` с переданным ``now``.
    """

    _store: dict[str, str] = {}

    def __init__(self) -> None:
        # Все инстансы шарят один dict — этим симулируем «два worker'а на
        # один Redis». Reset делается тестом через ``reset_store``.
        pass

    @classmethod
    def reset_store(cls) -> None:
        cls._store.clear()

    async def eval(self, script: str, n: int, *args: str):  # noqa: PLR0911, PLR0912
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
                # Переход в half_open: snapshot state.
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
    """Подменяет ``_get_client`` в bmc_circuit_breaker на FakeRedis-фабрику."""

    FakeRedis.reset_store()

    async def fake_get_client():
        return FakeRedis()

    monkeypatch.setattr(cb, "_get_client", fake_get_client)
    return FakeRedis


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Контроль ``time.time()`` внутри bmc_circuit_breaker.

    Возвращает dict ``{"now": float}`` — тесты двигают ``now`` вперёд для
    эмуляции истечения cooldown'а. Без freeze тесты были бы flaky.
    """
    state = {"now": 1_700_000_000.0}

    def fake_time() -> float:
        return state["now"]

    monkeypatch.setattr(cb.time, "time", fake_time)
    return state


# ── Тесты ────────────────────────────────────────────────────────────────────


class TestClosedToOpenTransition:
    """Threshold failure'ов закрытого breaker'а открывает его."""

    async def test_closed_state_check_passes(self, fake_redis, frozen_clock) -> None:
        await cb.check("10.0.0.1")  # no exception → closed by default

    async def test_failure_under_threshold_keeps_closed(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.2"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD - 1):
            await cb.record_failure(host)
        # All under-threshold failures — breaker still closed.
        await cb.check(host)  # no raise

    async def test_failure_at_threshold_opens(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.3"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        with pytest.raises(cb.CircuitBreakerOpenError) as ei:
            await cb.check(host)
        assert ei.value.details["host"] == host
        # retry_after примерно cooldown_seconds (≤30s по дефолту).
        assert 0 < ei.value.details["retry_after_seconds"] <= cb.DEFAULT_COOLDOWN_SECONDS


class TestOpenRejects:
    """Open breaker отбивает запросы до истечения cooldown."""

    async def test_open_keeps_rejecting_during_cooldown(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.4"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        # Несколько проверок подряд внутри одного окна — все отбиваются.
        for _ in range(3):
            with pytest.raises(cb.CircuitBreakerOpenError):
                await cb.check(host)


class TestHalfOpenRecovery:
    """После cooldown'а breaker переходит в half_open и пропускает пробу."""

    async def test_after_cooldown_transitions_to_half_open(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.5"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        # Прыгнули за окно cooldown'а.
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1
        # Первый запрос — должен пройти как пробный (half_open).
        await cb.check(host)  # no raise
        # State должен стать half_open в store.
        _, state_key, _ = cb._keys(host)
        assert fake_redis._store.get(state_key) == "half_open"

    async def test_half_open_success_closes_breaker(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.6"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1
        await cb.check(host)  # half_open
        await cb.record_success(host)
        # Дальше — closed: счётчик чист, state снят.
        failures_key, state_key, open_until_key = cb._keys(host)
        assert state_key not in fake_redis._store
        assert open_until_key not in fake_redis._store
        assert failures_key not in fake_redis._store
        # И ничего не бросает.
        await cb.check(host)

    async def test_half_open_failure_reopens(
        self, fake_redis, frozen_clock,
    ) -> None:
        """Fail в half_open должен заново открыть breaker.

        Это менее очевидный case: после cooldown'а пробный запрос в реальном
        BMC может снова упасть (контроллер не починили). С `record_failure`
        мы накопим threshold заново, но т.к. half_open уже близок к open —
        одного fail'а часто хватает. Реализация: record_failure инкрементит
        счётчик failures с нуля (мы его сбросили при open-transition);
        тут проверяем что для повторного open нужно опять threshold fail'ов,
        и тогда breaker уйдёт в open снова.
        """
        host = "10.0.0.7"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1
        await cb.check(host)  # half_open
        # Снова N failure'ов — breaker открывается.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check(host)


class TestMultiReplicaSharing:
    """State одного host'а виден из «другого» worker'а (тот же Redis).

    FakeRedis._store шарится между instance'ами — это и есть имитация двух
    реплик: failures, накопленные в одной, видны другой.
    """

    async def test_failures_visible_across_replicas(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.8"
        # «Реплика A» накапливает половину threshold'а.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD // 2):
            await cb.record_failure(host)
        # «Реплика B» (новый клиент, тот же store) добавляет оставшиеся +1.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD - cb.DEFAULT_FAILURE_THRESHOLD // 2):
            await cb.record_failure(host)
        # Любая «реплика» теперь видит open.
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check(host)

    async def test_success_from_one_replica_closes_for_all(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.9"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1
        await cb.check(host)  # реплика-A берёт половинку: half_open
        await cb.record_success(host)  # реплика-A репортит успех
        # Реплика-B сразу видит closed — никаких исключений.
        await cb.check(host)


class TestRedisFailureFailOpen:
    """Если Redis недоступен — breaker не блокирует BMC-вызовы (fail-open)."""

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
        # check не должен бросать — fail-open.
        await cb.check("10.0.0.10")

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
        # record_failure не должен бросать.
        await cb.record_failure("10.0.0.11")


class TestReset:
    """Operator helper: ``reset(host)`` снимает state досрочно."""

    async def test_reset_drops_open_state(
        self, fake_redis, frozen_clock,
    ) -> None:
        host = "10.0.0.12"
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure(host)
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check(host)
        await cb.reset(host)
        await cb.check(host)  # no raise


class TestEmptyHostNoop:
    """Пустой host не должен дергать Redis вообще — defensive guard."""

    async def test_check_noop_on_empty_host(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"n": 0}

        async def fake_get_client():
            called["n"] += 1
            raise AssertionError("should not be called for empty host")

        monkeypatch.setattr(cb, "_get_client", fake_get_client)
        await cb.check("")
        assert called["n"] == 0
