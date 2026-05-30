"""Регрессия: один fail в half_open сразу возвращает breaker в open.

Раньше `RECORD_FAILURE_SCRIPT` не различал closed и half_open: при
fail'е в half_open оно просто INCR'ило счётчик failures с нуля и
требовало накопить threshold заново. На default'ах 5/60/30 это
означало, что за один cooldown breaker пропустил бы до 5
проваливающихся пробных запросов в умирающий канал — ровно то, от
чего breaker должен защищать.

После фикса в Lua-скрипте проверяется ветка `current_state == 'half_open'`
и сразу делается переход в open + reset counter + set open_until.

Тест дёргаем оба breaker'а (bmc и audit_publisher), потому что Lua —
общий (`_breaker_lua.RECORD_FAILURE_SCRIPT`), но интеграция через
key-set'ы и обвязку у них раздельная.
"""

from __future__ import annotations

import pytest

from src.services import audit_publisher_breaker as apb
from src.services import bmc_circuit_breaker as bmc
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


@pytest.fixture
def fake_redis_apb(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, apb)


@pytest.fixture
def frozen_clock_apb(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, apb)


@pytest.fixture
def fake_redis_bmc(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, bmc)


@pytest.fixture
def frozen_clock_bmc(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, bmc)


async def test_audit_publisher_half_open_one_failure_reopens(
    fake_redis_apb, frozen_clock_apb,
):
    """audit_publisher_breaker: один fail в half_open → state=open сразу."""
    # Доводим до open.
    for _ in range(apb.DEFAULT_FAILURE_THRESHOLD):
        await apb.record_failure()
    failures_key, state_key, open_until_key = apb._keys()
    assert fake_redis_apb._store.get(state_key) == "open"

    # Прыгаем за cooldown — следующий check переведёт в half_open.
    frozen_clock_apb["now"] += apb.DEFAULT_COOLDOWN_SECONDS + 1
    await apb.check()  # half_open
    assert fake_redis_apb._store.get(state_key) == "half_open"

    # Один fail в half_open — сразу обратно в open.
    await apb.record_failure()
    assert fake_redis_apb._store.get(state_key) == "open"
    # Counter обнулён — стандартный CB-pattern.
    assert failures_key not in fake_redis_apb._store
    # open_until обновлён вперёд от текущего now на cooldown.
    open_until = int(fake_redis_apb._store[open_until_key])
    assert open_until == int(frozen_clock_apb["now"]) + apb.DEFAULT_COOLDOWN_SECONDS

    # check() в open-окне отбивает.
    with pytest.raises(apb.CircuitBreakerOpenError):
        await apb.check()


async def test_bmc_half_open_one_failure_reopens(
    fake_redis_bmc, frozen_clock_bmc,
):
    """bmc_circuit_breaker: один fail в half_open → state=open сразу (per-host)."""
    host = "10.0.0.42"
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host)
    failures_key, state_key, open_until_key = bmc._keys(host)
    assert fake_redis_bmc._store.get(state_key) == "open"

    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1
    await bmc.check(host)  # half_open
    assert fake_redis_bmc._store.get(state_key) == "half_open"

    await bmc.record_failure(host)
    assert fake_redis_bmc._store.get(state_key) == "open"
    assert failures_key not in fake_redis_bmc._store
    open_until = int(fake_redis_bmc._store[open_until_key])
    assert open_until == int(frozen_clock_bmc["now"]) + bmc.DEFAULT_COOLDOWN_SECONDS

    with pytest.raises(bmc.CircuitBreakerOpenError):
        await bmc.check(host)
