"""Регрессия: open→half_open пропускает ровно одну реплику, не толпу.

До фикса `CHECK_SCRIPT` при истекшем cooldown'е переключал state в
half_open и возвращал `(half_open, 0)` любому, кто пришёл первым. Но
все остальные реплики, прибежавшие в течение того же tick'а, видели
state == "half_open" и тоже получали `(half_open, 0)` — в итоге в
едва ожившие BMC / loging_service летел залп параллельных запросов
(thundering herd). На рестарте downstream'а это с большой долей
вероятности роняло его обратно.

После фикса в `CHECK_SCRIPT` появился probe-ключ
(`cb:bmc:<host>:probe` / `cb:audit_publisher:probe`). Захват — через
SET NX EX cooldown. Победитель SETNX переключает state в half_open
и идёт делать пробный запрос; проигравшие видят probe в Redis и
получают `(open, ttl_probe)` — стандартный «попробуй позже».

Тест дёргает оба breaker'а (bmc и audit_publisher): Lua общий
(`_breaker_lua.CHECK_SCRIPT`), но обвязка и key-set'ы у них раздельные.
"""

from __future__ import annotations

import asyncio

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


# ── audit_publisher_breaker ────────────────────────────────────────────────


async def test_audit_publisher_only_one_check_enters_half_open(
    fake_redis_apb, frozen_clock_apb,
):
    """10 параллельных check() в момент open→half_open → ровно 1 проходит."""
    # Доводим до open.
    for _ in range(apb.DEFAULT_FAILURE_THRESHOLD):
        await apb.record_failure()
    # Прыгаем за cooldown.
    frozen_clock_apb["now"] += apb.DEFAULT_COOLDOWN_SECONDS + 1

    # 10 параллельных check'ов. Один должен пройти (победитель SETNX),
    # остальные 9 — отбиться с CircuitBreakerOpenError.
    results = await asyncio.gather(
        *(_safe_check_apb() for _ in range(10)),
        return_exceptions=False,
    )
    passed = [r for r in results if r is None]
    rejected = [r for r in results if isinstance(r, apb.CircuitBreakerOpenError)]
    assert len(passed) == 1, f"один check должен пройти; got passed={len(passed)}"
    assert len(rejected) == 9, f"девять должны отбиться; got rejected={len(rejected)}"
    # retry_after у отбитых — положительный (TTL probe-ключа).
    for err in rejected:
        assert err.details["retry_after_seconds"] > 0


async def test_audit_publisher_success_releases_probe_slot(
    fake_redis_apb, frozen_clock_apb,
):
    """record_success после half_open → следующий check снова получает half_open."""
    for _ in range(apb.DEFAULT_FAILURE_THRESHOLD):
        await apb.record_failure()
    frozen_clock_apb["now"] += apb.DEFAULT_COOLDOWN_SECONDS + 1

    # Первый check — winner, проходит.
    await apb.check()

    # record_success → полный reset (вкл. probe).
    await apb.record_success()
    _, state_key, open_until_key = apb._keys()
    probe_key = apb._probe_key()
    assert state_key not in fake_redis_apb._store
    assert open_until_key not in fake_redis_apb._store
    assert probe_key not in fake_redis_apb._store
    # Следующий check — closed (никаких ключей).
    await apb.check()


async def test_audit_publisher_failure_releases_probe_slot(
    fake_redis_apb, frozen_clock_apb,
):
    """fail в half_open → state=open, probe-ключ снят, следующий cycle чистый."""
    for _ in range(apb.DEFAULT_FAILURE_THRESHOLD):
        await apb.record_failure()
    frozen_clock_apb["now"] += apb.DEFAULT_COOLDOWN_SECONDS + 1
    await apb.check()  # winner

    # Пробный запрос провалился.
    await apb.record_failure()
    _, state_key, _ = apb._keys()
    probe_key = apb._probe_key()
    assert fake_redis_apb._store.get(state_key) == "open"
    assert probe_key not in fake_redis_apb._store

    # Внутри cooldown check отбивается.
    with pytest.raises(apb.CircuitBreakerOpenError):
        await apb.check()

    # Прыгаем за новый cooldown — снова можем зайти как winner.
    frozen_clock_apb["now"] += apb.DEFAULT_COOLDOWN_SECONDS + 1
    await apb.check()
    assert fake_redis_apb._store.get(state_key) == "half_open"
    assert probe_key in fake_redis_apb._store


async def _safe_check_apb():
    try:
        await apb.check()
    except apb.CircuitBreakerOpenError as e:
        return e
    return None


# ── bmc_circuit_breaker ────────────────────────────────────────────────────


async def test_bmc_only_one_check_enters_half_open(
    fake_redis_bmc, frozen_clock_bmc,
):
    """10 параллельных check(host) в момент open→half_open → ровно 1 проходит."""
    host = "10.0.0.42"
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host)
    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1

    results = await asyncio.gather(
        *(_safe_check_bmc(host) for _ in range(10)),
        return_exceptions=False,
    )
    passed = [r for r in results if r is None]
    rejected = [r for r in results if isinstance(r, bmc.CircuitBreakerOpenError)]
    assert len(passed) == 1
    assert len(rejected) == 9
    for err in rejected:
        assert err.details["retry_after_seconds"] > 0
        assert err.details["host"] == host


async def test_bmc_success_releases_probe_slot(
    fake_redis_bmc, frozen_clock_bmc,
):
    host = "10.0.0.43"
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host)
    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1

    await bmc.check(host)
    await bmc.record_success(host)
    failures_key, state_key, open_until_key = bmc._keys(host)
    probe_key = bmc._probe_key(host)
    assert failures_key not in fake_redis_bmc._store
    assert state_key not in fake_redis_bmc._store
    assert open_until_key not in fake_redis_bmc._store
    assert probe_key not in fake_redis_bmc._store
    await bmc.check(host)


async def test_bmc_failure_releases_probe_slot(
    fake_redis_bmc, frozen_clock_bmc,
):
    host = "10.0.0.44"
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host)
    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1
    await bmc.check(host)  # winner

    await bmc.record_failure(host)
    _, state_key, _ = bmc._keys(host)
    probe_key = bmc._probe_key(host)
    assert fake_redis_bmc._store.get(state_key) == "open"
    assert probe_key not in fake_redis_bmc._store

    with pytest.raises(bmc.CircuitBreakerOpenError):
        await bmc.check(host)

    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1
    await bmc.check(host)
    assert fake_redis_bmc._store.get(state_key) == "half_open"
    assert probe_key in fake_redis_bmc._store


async def test_bmc_probe_isolation_between_hosts(
    fake_redis_bmc, frozen_clock_bmc,
):
    """probe-ключ per-host: probe на host A не блокирует half_open на host B."""
    host_a = "10.0.0.50"
    host_b = "10.0.0.51"
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host_a)
    for _ in range(bmc.DEFAULT_FAILURE_THRESHOLD):
        await bmc.record_failure(host_b)
    frozen_clock_bmc["now"] += bmc.DEFAULT_COOLDOWN_SECONDS + 1

    # Оба host'а независимо проходят как winner.
    await bmc.check(host_a)
    await bmc.check(host_b)
    _, state_key_a, _ = bmc._keys(host_a)
    _, state_key_b, _ = bmc._keys(host_b)
    assert fake_redis_bmc._store.get(state_key_a) == "half_open"
    assert fake_redis_bmc._store.get(state_key_b) == "half_open"


async def _safe_check_bmc(host: str):
    try:
        await bmc.check(host)
    except bmc.CircuitBreakerOpenError as e:
        return e
    return None
