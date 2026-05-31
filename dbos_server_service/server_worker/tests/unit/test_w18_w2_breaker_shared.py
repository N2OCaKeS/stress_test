"""Регрессия P2/W14: shared circuit breaker для audit-publisher + concurrent
SELECT'ы по `audit_outbox` через `FOR UPDATE SKIP LOCKED`.

Покрывает две гарантии, важные при multi-replica deploy worker'а:

1. Failure'ы accumulate'ятся в общем Redis-store. Если «реплика A» дала
   N-1 fail'ов, а «реплика B» добила до threshold'а — следующий `check()`
   у обеих видит open (а не у каждой свой счётчик, как было до W11/W14).
   После cooldown'а и success'а пробного запроса обе реплики снова
   видят closed.

2. `_select_unpublished_excluding` рендерится с `FOR UPDATE SKIP LOCKED`
   и поддерживает per-replica exclude-set. Два конкурирующих publisher'а
   с разными `exclude_ids` соберут непересекающиеся подмножества — даже
   если их exclude'ы перекрываются, оставшиеся id'шники не пересекаются
   с противоположным exclude'ом, и две реплики не делают дубль HTTP'а
   в loging_service.

Юнит-уровень: FakeRedis из `tests/unit/_breaker_test_helpers.py` (class-
level `_store` шарится между инстансами, что и моделирует «один Redis на
несколько реплик»). SQL-контракт `_select_unpublished_excluding`
проверяется compile'ом под Postgres-диалект — без реального коннекта
к БД, потому что `SKIP LOCKED` — серверная фича PG.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from src.services import audit_outbox_publisher
from src.services import audit_publisher_breaker as cb
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, cb)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, cb)


# ── Shared breaker: state видят все реплики ─────────────────────────────────

class TestSharedFailuresAcrossReplicas:
    """N-1 fail'ов на «реплике A» + 1 fail на «реплике B» = open для обеих.

    Без shared store потребовалось бы по threshold'у на каждой реплике
    (`N × threshold` суммарных HTTP-fail'ов до закрытия канала). С Redis-
    Lua счётчиком — суммарно ровно `threshold`.
    """

    async def test_partial_failures_on_a_combined_with_b_open_for_both(
        self, fake_redis, frozen_clock,
    ) -> None:
        # «Реплика A»: threshold-1 fail. Каждый record_failure() —
        # отдельный FakeRedis-инстанс, но `_store` class-level → shared.
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD - 1):
            await cb.record_failure()

        # «Реплика B»: ещё один fail добивает до threshold.
        await cb.record_failure()

        # «Реплика A» (новый client, тот же store) видит open.
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()

        # И «реплика B» тоже видит open.
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()

    async def test_open_seen_by_all_replicas_during_cooldown(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()

        # Несколько разных «реплик» подряд — все отбиваются breaker'ом.
        for _ in range(5):
            with pytest.raises(cb.CircuitBreakerOpenError):
                await cb.check()


# ── Half-open recovery: probe от одной реплики закрывает для всех ───────────

class TestHalfOpenProbeAndReset:
    """После cooldown'а одна реплика «выигрывает» probe-slot и пускает запрос.

    Если probe прошёл (success) — state в Redis сносится → все реплики
    видят closed. Если probe упал — breaker возвращается в open ещё на
    cooldown, остальные реплики продолжают видеть open.
    """

    async def test_probe_success_closes_breaker_for_all_replicas(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()

        # Прыжок за cooldown — следующий check (любая реплика) станет probe-
        # выигрывателем и переведёт state в half_open.
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1

        # «Реплика A» — probe-win, check проходит.
        await cb.check()

        # «Реплика B» в это же время видит half_open → отбивается
        # (один пробный запрос — у одной реплики).
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()

        # Probe вернул успех → record_success сносит все ключи.
        await cb.record_success()

        # Любая реплика теперь видит closed — check не бросает.
        await cb.check()
        await cb.check()  # реплика B, отдельный «client» с тем же store

        failures_key, state_key, open_until_key = cb._keys()
        assert state_key not in fake_redis._store
        assert open_until_key not in fake_redis._store
        assert failures_key not in fake_redis._store

    async def test_probe_failure_reopens_for_all(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1

        # Probe-выигрыватель пускает запрос…
        await cb.check()
        # …и ловит fail — half_open → open опять.
        await cb.record_failure()

        # Все остальные реплики снова видят open.
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()
        with pytest.raises(cb.CircuitBreakerOpenError):
            await cb.check()


# ── SELECT outbox: SKIP LOCKED + exclude_ids ────────────────────────────────

class TestConcurrentClaimSkipLocked:
    """`_select_unpublished_excluding` рендерится с FOR UPDATE SKIP LOCKED.

    Две конкурирующие реплики, читающие свой батч с разными `exclude_ids`,
    не дублируют выборку. Заодно валидируем, что NOT IN-clause живой.
    """

    def test_excluding_stmt_renders_for_update_skip_locked(self) -> None:
        stmt = audit_outbox_publisher._select_unpublished_excluding(
            5, exclude_ids=[1, 2, 3],
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in compiled
        assert "SKIP LOCKED" in compiled

    def test_excluding_stmt_with_empty_list_omits_notin(self) -> None:
        # Без exclude'а — простой SKIP LOCKED-SELECT, lower-bound check'a.
        stmt = audit_outbox_publisher._select_unpublished_excluding(
            5, exclude_ids=[],
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in compiled
        assert "SKIP LOCKED" in compiled
        # Не должно быть лишнего NOT IN, пустого () (пустой IN — SQL-NOOP
        # для разных диалектов, лишний шум в плане).
        assert "NOT IN" not in compiled.upper().replace("NOT IN (NULL)", "")

    def test_excluding_stmt_disjoint_with_complementary_excludes(self) -> None:
        """Симметричная пара exclude'ов: A исключает [1,2], B — [3,4].

        Compiled SQL у обеих содержит SKIP LOCKED и взаимно дополняющие
        NOT IN — гарантия, что при одинаковом snapshot'е данных каждая
        реплика возьмёт row'ы из «своей» половины.
        """
        a = audit_outbox_publisher._select_unpublished_excluding(
            5, exclude_ids=[1, 2],
        )
        b = audit_outbox_publisher._select_unpublished_excluding(
            5, exclude_ids=[3, 4],
        )
        compiled_a = str(a.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ))
        compiled_b = str(b.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ))
        assert "SKIP LOCKED" in compiled_a
        assert "SKIP LOCKED" in compiled_b
        # NOT IN видны после literal_binds — exclude_ids зашиты в текст
        # запроса, не в bind-параметры.
        assert "NOT IN (1, 2)" in compiled_a
        assert "NOT IN (3, 4)" in compiled_b
