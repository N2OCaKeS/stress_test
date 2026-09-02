"""End-to-end проверка skip'а row'и в `_publish_one` под open breaker'ом.

Регрессия: после перехода на shared `audit_publisher_breaker` (Lua-
скрипты в Redis) надо убедиться, что full path работает:

  * пять подряд `record_failure()` копят счётчик в FakeRedis →
    breaker уходит в open;
  * следующий `_publish_one` вызывает `check()` и получает
    `CircuitBreakerOpenError`;
  * row остаётся unpublished (published_at=None), `attempts` НЕ
    инкрементируется (breaker-skip ≠ попытка доставки);
  * adaptive sleep в `run_publisher_loop` отрабатывает корректно —
    `get_state()` тоже возвращает open + retry_after > 0.

В отличие от `unit/test_audit_publisher_breaker.py` (изолированные
Lua-state tests) тут проверяем интеграцию publisher'а с FakeRedis-
шиной — тот самый full path до выбора row из БД.
"""

from __future__ import annotations

import pytest

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher, audit_publisher_breaker
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, audit_publisher_breaker)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, audit_publisher_breaker)


async def _insert_outbox_row(action: str = "server.power_on") -> int:
    async with AsyncSessionLocal() as session:
        row = AuditOutbox(
            task_id="tsk_breaker_test",
            payload={"action": action, "target_id": "srv_x"},
        )
        session.add(row)
        await session.commit()
        return row.id


async def _fetch_row(row_id: int) -> AuditOutbox | None:
    async with AsyncSessionLocal() as session:
        return await session.get(AuditOutbox, row_id)


async def test_publish_one_breaker_open_skip_real_redis(
    fake_redis, frozen_clock, monkeypatch,
):
    """Open breaker → row не публикуется, attempts не растут, get_state() open.

    Полный integration-flow через FakeRedis:
      1. INSERT unpublished outbox-row.
      2. Доводим breaker до open через 5×`record_failure()`.
      3. Подменяем `audit_client.emit` на «должен взорваться, если вызвался» —
         так доказываем, что check() реально отбил вызов до сети.
      4. Запускаем `flush_outbox()`; ожидаем published=0.
      5. Проверяем row: published_at=None, attempts=0.
      6. Проверяем `get_state()` → ("open", >0).
    """
    row_id = await _insert_outbox_row()

    # Доводим breaker до open.
    for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
        await audit_publisher_breaker.record_failure()

    # emit не должен вызваться — check() отбивает до HTTP'а.
    emit_called = {"n": 0}

    async def boom_emit(action, **kw):
        emit_called["n"] += 1
        raise AssertionError("emit() must not be called under open breaker")

    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.audit_client.emit", boom_emit,
    )

    published = await audit_outbox_publisher.flush_outbox()

    assert published == 0
    assert emit_called["n"] == 0

    row = await _fetch_row(row_id)
    assert row is not None
    assert row.published_at is None, "open breaker не должен публиковать row"
    assert row.attempts == 0, (
        f"breaker-skip ≠ попытка доставки, attempts должен остаться 0; got={row.attempts}"
    )

    # get_state видит open + положительный retry_after — adaptive-sleep
    # в loop'е получит то, что ожидает.
    state, retry_after = await audit_publisher_breaker.get_state()
    assert state == "open"
    assert retry_after > 0
