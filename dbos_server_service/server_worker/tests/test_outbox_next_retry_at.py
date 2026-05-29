"""Тесты per-row exponential backoff в audit_outbox publisher'е.

Что проверяем:

* `next_retry_at` ставится при HTTP-failure и при программной ошибке;
* row с `next_retry_at > now()` не выбирается publisher'ом;
* row с `next_retry_at <= now()` снова доступен;
* успешный publish обнуляет `next_retry_at`;
* DLQ-row (`permanent_4xx`, `missing_action`) `next_retry_at` НЕ ставит
  (там row уже закрыт через `_send_to_dlq`);
* delay растёт по `2^attempts` и капается `_BACKOFF_MAX_SECONDS`;
* `re_attempt_row` сбрасывает `published_at`/`attempts`/`next_retry_at`/`last_error`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher
from src.services.audit_client import AuditEmitError


async def _all_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


async def _seed_one(payload: dict | None = None) -> int:
    payload = payload or {"action": "test.evt", "status": "success"}
    async with AsyncSessionLocal() as session:
        row = AuditOutbox(task_id=None, payload=payload)
        session.add(row)
        await session.commit()
        return row.id


class TestBackoffOnTransientFailure:
    async def test_5xx_sets_next_retry_at(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        await _seed_one()

        async def fail_5xx(action, **kw):
            raise AuditEmitError("HTTP 503", status_code=503)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail_5xx,
        )

        before = datetime.now(timezone.utc)
        published = await audit_outbox_publisher.flush_outbox(limit=1)
        assert published == 0

        rows = await _all_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.published_at is None
        assert row.attempts == 1
        assert row.next_retry_at is not None
        # 2^1 = 2s.
        assert row.next_retry_at >= before + timedelta(seconds=1)
        assert row.next_retry_at <= before + timedelta(seconds=10)

    async def test_programmatic_error_sets_next_retry_at(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        await _seed_one()

        async def boom(action, **kw):
            raise RuntimeError("serialization bug")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=1)
        assert published == 0
        rows = await _all_rows()
        assert rows[0].next_retry_at is not None
        assert rows[0].attempts == 1


class TestBackoffSkipsRow:
    async def test_row_with_future_next_retry_not_picked(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        # Руками отодвигаем next_retry_at в будущее.
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == rid)
                .values(next_retry_at=future, attempts=1)
            )
            await session.commit()

        called = {"n": 0}

        async def emit(action, **kw):
            called["n"] += 1

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=5)
        assert published == 0
        assert called["n"] == 0
        # Row остался unpublished.
        rows = await _all_rows()
        assert rows[0].published_at is None

    async def test_row_with_past_next_retry_is_picked(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == rid)
                .values(next_retry_at=past, attempts=1)
            )
            await session.commit()

        called = {"n": 0}

        async def emit(action, **kw):
            called["n"] += 1

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=5)
        assert published == 1
        assert called["n"] == 1


class TestSuccessClearsBackoff:
    async def test_success_resets_next_retry_at(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        # Симулируем «была попытка, был backoff».
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == rid)
                .values(next_retry_at=past, attempts=3)
            )
            await session.commit()

        async def ok(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            ok,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=1)
        assert published == 1

        rows = await _all_rows()
        assert rows[0].published_at is not None
        assert rows[0].next_retry_at is None


class TestDlqDoesNotSetBackoff:
    async def test_permanent_4xx_no_backoff(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        await _seed_one()

        async def fail_4xx(action, **kw):
            raise AuditEmitError("HTTP 422", status_code=422)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail_4xx,
        )

        before_dlq = audit_outbox_publisher.get_dlq_total()
        await audit_outbox_publisher.flush_outbox(limit=1)
        assert audit_outbox_publisher.get_dlq_total() == before_dlq + 1

        rows = await _all_rows()
        # DLQ: published_at стоит, next_retry_at — никто не выставлял.
        assert rows[0].published_at is not None
        assert rows[0].next_retry_at is None

    async def test_missing_action_no_backoff(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        await _seed_one(payload={"status": "success"})  # без 'action'

        async def emit(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            emit,
        )

        await audit_outbox_publisher.flush_outbox(limit=1)
        rows = await _all_rows()
        assert rows[0].published_at is not None
        assert rows[0].next_retry_at is None


class TestBackoffGrowth:
    async def test_delay_grows_with_attempts(self, monkeypatch):
        # Эмулируем разный начальный `attempts` → разный delay.
        # `_apply_backoff` использует уже инкрементнутый `attempts`,
        # т.е. на первой неудаче attempts=1 → 2s, на пятой → 32s.
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        # Преднаставим attempts=4 — после fail'а станет 5 → delay 2^5=32s.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == rid)
                .values(attempts=4)
            )
            await session.commit()

        async def fail(action, **kw):
            raise AuditEmitError("HTTP 503", status_code=503)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail,
        )

        before = datetime.now(timezone.utc)
        await audit_outbox_publisher.flush_outbox(limit=1)

        rows = await _all_rows()
        row = rows[0]
        assert row.attempts == 5
        # 2^5 = 32s.
        assert row.next_retry_at >= before + timedelta(seconds=30)
        # С запасом на тест-машину — но точно меньше cap.
        assert row.next_retry_at <= before + timedelta(seconds=60)

    async def test_delay_capped_at_max(self, monkeypatch):
        # Высокий attempts → 2^attempts >> cap → cap'аем.
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == rid)
                .values(attempts=19)  # → 20 после fail'а, 2^20 = 1048576s
            )
            await session.commit()

        async def fail(action, **kw):
            raise AuditEmitError("HTTP 503", status_code=503)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail,
        )

        before = datetime.now(timezone.utc)
        await audit_outbox_publisher.flush_outbox(limit=1)

        rows = await _all_rows()
        row = rows[0]
        # cap = 300s. next_retry_at должен быть ≤ before+300s+slack.
        assert row.next_retry_at <= before + timedelta(seconds=310)
        assert row.next_retry_at >= before + timedelta(seconds=295)


class TestReAttemptRow:
    async def test_re_attempt_resets_dlq_row(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()

        async def fail_4xx(action, **kw):
            raise AuditEmitError("HTTP 422", status_code=422)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail_4xx,
        )
        await audit_outbox_publisher.flush_outbox(limit=1)

        rows = await _all_rows()
        assert rows[0].published_at is not None  # DLQ
        assert rows[0].last_error  # truthy после 4xx

        ok = await audit_outbox_publisher.re_attempt_row(rid)
        assert ok is True

        rows = await _all_rows()
        row = rows[0]
        assert row.published_at is None
        assert row.attempts == 0
        assert row.next_retry_at is None
        assert row.last_error is None

    async def test_re_attempt_missing_row_returns_false(self):
        ok = await audit_outbox_publisher.re_attempt_row(999_999_999)
        assert ok is False

    async def test_re_attempt_already_unpublished_returns_false(self):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_one()
        # Row только-только вставлена — published_at IS NULL.
        ok = await audit_outbox_publisher.re_attempt_row(rid)
        assert ok is False
