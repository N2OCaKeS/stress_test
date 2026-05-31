"""Регрессия: `_flush_outbox_once` под open breaker'ом bail-out'ит сразу.

Раньше при open breaker'е `_publish_one` возвращал
`PublishResult(closed=True, was_published=False, audit_emit_error=False)`,
но row при этом НЕ попадал в `failed_ids` (closed → не отмечается как
failed). Следующая итерация SELECT'а возвращала ту же row (она же
по-прежнему `published_at IS NULL`), и весь `limit=5`-цикл крутил
SELECT/breaker-skip/SELECT/... по одной и той же row'е.

После фикса в `_publish_one` появился флаг `breaker_skipped`, по
которому `_flush_outbox_once` break'ает из batch'-loop'а. Background
poll-loop вернётся через ≤ `_CB_SLEEP_CHUNK_SECONDS`.

В тесте кладём в outbox 5 row, доводим breaker до open, дёргаем
`flush_outbox`, ждём ровно 1 select-вызов (а не 5).
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


async def _insert_outbox_rows(n: int) -> list[int]:
    ids: list[int] = []
    async with AsyncSessionLocal() as session:
        for i in range(n):
            row = AuditOutbox(
                task_id=f"tsk_bailout_{i}",
                payload={"action": "server.power_on", "target_id": f"srv_{i}"},
            )
            session.add(row)
        await session.commit()
        # Подхватим id'шники для верификации.
        from sqlalchemy import select
        res = await session.execute(
            select(AuditOutbox.id).order_by(AuditOutbox.id.asc())
        )
        ids = [r for r in res.scalars().all()]
    return ids


async def test_open_breaker_bails_out_after_first_row(
    fake_redis, frozen_clock, monkeypatch,
):
    """5 row'ов в outbox + open breaker → flush_outbox publishes 0 и трогает
    максимум одну row (breaker_skipped → break из loop'а).

    Раньше: 5 итераций, 5 одинаковых SELECT'ов одной и той же row'и.
    """
    await _insert_outbox_rows(5)

    # Чистый старт счётчика skip'ов: фикс'ы могли инкрементить его в
    # предыдущих тестах модуля.
    audit_outbox_publisher._reset_breaker_state()
    skips_before = audit_outbox_publisher.get_breaker_skips_total()

    # Доводим breaker до open.
    for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
        await audit_publisher_breaker.record_failure()

    # emit ловим — он не должен вызываться вообще, check() отбивает.
    emit_calls = {"n": 0}

    async def boom_emit(action, **kw):
        emit_calls["n"] += 1
        raise AssertionError("emit() must not be called under open breaker")

    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.audit_client.emit", boom_emit,
    )

    # Считаем количество вызовов внутреннего SELECT'а через спай на
    # `_select_unpublished_excluding`. Раньше было `limit=5` итераций
    # (полный обход), сейчас — ровно одна (break после breaker_skipped).
    select_calls = {"n": 0}
    original_select = audit_outbox_publisher._select_unpublished_excluding

    def counted_select(limit, exclude_ids):
        select_calls["n"] += 1
        return original_select(limit, exclude_ids)

    monkeypatch.setattr(
        audit_outbox_publisher,
        "_select_unpublished_excluding",
        counted_select,
    )

    published = await audit_outbox_publisher.flush_outbox()

    assert published == 0
    assert emit_calls["n"] == 0
    # Ключевая проверка: только ОДИН SELECT за весь flush, а не пять.
    assert select_calls["n"] == 1, (
        f"flush должен bail-out'ить после первого breaker-skip; got selects={select_calls['n']}"
    )

    # Все 5 row'ов остались unpublished, ни одной не подняли attempts.
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        rows = (
            await session.execute(
                select(AuditOutbox).order_by(AuditOutbox.id.asc())
            )
        ).scalars().all()
        assert len(rows) == 5
        for row in rows:
            assert row.published_at is None
            assert row.attempts == 0, (
                f"breaker-skip ≠ попытка доставки, row {row.id} attempts={row.attempts}"
            )

    # Метрика breaker-skip: ровно один skip (одна row дошла до check()),
    # дальше flush bail-out'ит.
    assert (
        audit_outbox_publisher.get_breaker_skips_total() - skips_before == 1
    ), (
        "ровно один skip должен быть зарегистрирован; "
        f"got delta={audit_outbox_publisher.get_breaker_skips_total() - skips_before}"
    )
