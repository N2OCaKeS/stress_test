"""Контракт `PublishResult` — caller обращается по имени, не по позиции.

`_publish_one` возвращает три независимых bool-сигнала
(`closed`, `audit_emit_error`, `was_published`). До рефакторинга это
был обычный кортеж — позиции легко путались, особенно `closed` и
`was_published` (оба True на успехе, оба False на программной ошибке).

NamedTuple даёт обратную совместимость по unpacking'у (на случай если
где-то остался legacy-каллер), но в новом коде caller обязан читать по
имени поля.
"""

from __future__ import annotations

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher
from src.services.audit_client import AuditEmitError
from src.services.audit_outbox_publisher import PublishResult, _publish_one


async def _insert_outbox(payload: dict) -> int:
    """Создать одну outbox-row напрямую — нам нужна row, а не lifecycle."""
    async with AsyncSessionLocal() as session:
        row = AuditOutbox(payload=payload)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


class TestPublishResultShape:
    async def test_returns_named_tuple(self, monkeypatch):
        """Базовая sanity-проверка: результат — `PublishResult`,
        доступен и по имени, и по позиции (NamedTuple-совместимость).
        """
        audit_outbox_publisher._reset_breaker_state()

        async def ok_emit(action, **kw):
            return None

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            ok_emit,
        )

        row_id = await _insert_outbox({"action": "server.power_on", "status": "success"})

        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, row_id)
            res = await _publish_one(session, row)
            await session.commit()

        assert isinstance(res, PublishResult)
        # Named access — основной контракт.
        assert res.closed is True
        assert res.audit_emit_error is False
        assert res.was_published is True
        assert res.breaker_skipped is False
        # Positional access всё ещё работает — NamedTuple это даёт бесплатно,
        # держим как safety-net на случай legacy-кода в integration-тестах.
        closed, audit_emit_error, was_published, breaker_skipped = res
        assert (closed, audit_emit_error, was_published, breaker_skipped) == (
            True, False, True, False,
        )


class TestPublishResultBranches:
    """Проверяем, что три семантически разные ветки дают разные значения
    полей. Это и тест на отсутствие путаницы в порядке аргументов после
    миграции с tuple на NamedTuple.
    """

    async def test_success_branch(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()

        async def ok_emit(action, **kw):
            return None

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            ok_emit,
        )

        row_id = await _insert_outbox(
            {"action": "server.power_on", "status": "success"}
        )
        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, row_id)
            res = await _publish_one(session, row)
            await session.commit()

        assert res.closed is True
        assert res.was_published is True
        assert res.audit_emit_error is False

    async def test_missing_action_dlq_branch(self, monkeypatch):
        """Битый payload без action → DLQ, was_published=False,
        audit_emit_error=False (loging-канал не виноват).
        """
        audit_outbox_publisher._reset_breaker_state()

        emit_calls = []

        async def tracking_emit(action, **kw):
            emit_calls.append(action)
            return None

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            tracking_emit,
        )

        row_id = await _insert_outbox({"status": "success"})  # no action

        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, row_id)
            res = await _publish_one(session, row)
            await session.commit()

        assert res.closed is True
        assert res.was_published is False
        assert res.audit_emit_error is False
        # HTTP-emit не дёргали — payload отбит до сети.
        assert emit_calls == []

    async def test_http_5xx_transient_branch(self, monkeypatch):
        """5xx → row остаётся unpublished, closed=False, это сигнал breaker'у.
        """
        audit_outbox_publisher._reset_breaker_state()

        async def boom_5xx(action, **kw):
            raise AuditEmitError("HTTP 503", status_code=503)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_5xx,
        )

        row_id = await _insert_outbox(
            {"action": "server.power_on", "status": "success"}
        )
        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, row_id)
            res = await _publish_one(session, row)
            await session.commit()

        assert res.closed is False
        assert res.was_published is False
        assert res.audit_emit_error is True

    async def test_http_4xx_permanent_dlq_branch(self, monkeypatch):
        """4xx → DLQ, closed=True, was_published=False; для breaker'а — НЕ
        AuditEmitError (loging жив, наш payload плох).
        """
        audit_outbox_publisher._reset_breaker_state()

        async def boom_4xx(action, **kw):
            raise AuditEmitError("HTTP 422", status_code=422)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_4xx,
        )

        row_id = await _insert_outbox(
            {"action": "server.power_on", "status": "success"}
        )
        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, row_id)
            res = await _publish_one(session, row)
            await session.commit()

        assert res.closed is True
        assert res.was_published is False
        assert res.audit_emit_error is False
