"""Тесты CLI-команды `python -m src.cli outbox-reattempt`.

Покрытие:
  * happy path — DLQ-row сбрасывается, audit-событие `audit.outbox_reattempt_manual`
    эмитится через outbox (severity=WARNING);
  * row не найден → команда возвращает False, audit не пишется;
  * row уже unpublished → False (idempotent, не плодим лишние audit-events).

DB-зависимые: пользуемся session-scoped fixture'ой из `tests/conftest.py`
(`_setup_db` + per-test TRUNCATE) — тесты живут под `tests/unit/`,
но conftest.py применяется ко всему tree.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from src.cli import outbox as cli_outbox
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher


async def _seed_dlq_row() -> int:
    """Положить в `audit_outbox` готовый DLQ-row.

    `published_at = now()` имитирует то, что `_send_to_dlq` сделал на
    предыдущей итерации publisher'а. `last_error` — текст причины.
    """
    async with AsyncSessionLocal() as session:
        row = AuditOutbox(
            task_id="tsk_dlq",
            payload={"action": "test.evt", "status": "success"},
            published_at=datetime.now(timezone.utc),
            attempts=50,
            last_error="permanent_4xx",
        )
        session.add(row)
        await session.commit()
        return row.id


async def _all_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


class TestCmdOutboxReattempt:
    async def test_resets_dlq_row_and_emits_manual_audit(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()
        rid = await _seed_dlq_row()

        # Чтобы `flush_outbox` в команде не дёргал реальный HTTP — глушим
        # `audit_client.emit`. После этого audit-row останется unpublished
        # (5xx-семантика мы не имитируем; мок просто молча возвращает).
        async def noop_emit(action, **kw):
            return None

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            noop_emit,
        )

        ok = await cli_outbox.cmd_outbox_reattempt(
            row_id=rid, reason="server_service redeployed", actor_id="usr_op",
        )
        assert ok is True

        rows = await _all_rows()
        # Ожидаем 2 row'и: исходный (сброшен), и новый — manual audit-event.
        assert len(rows) == 2

        # Исходный — после сброса: published_at IS NULL, attempts=0, last_error IS NULL.
        original = next(r for r in rows if r.id == rid)
        # Реальный happy-path `flush_outbox` с моком noop_emit сам отметит
        # row как published. Проверяем именно reset-эффект CLI: до flush'а
        # row была сброшена, после успешного fake-emit она снова published.
        # Достаточно убедиться, что attempts=0 и last_error пустой —
        # признаки именно reset'а, а не оригинального DLQ-state'а.
        assert original.attempts == 0
        assert original.last_error is None

        # Manual audit row — payload содержит action + severity + reason.
        manual = next(r for r in rows if r.id != rid)
        assert manual.payload["action"] == "audit.outbox_reattempt_manual"
        assert manual.payload["severity"] == "WARNING"
        assert manual.payload["target_id"] == str(rid)
        assert manual.payload["target_type"] == "audit_outbox"
        assert manual.payload["actor_id"] == "usr_op"
        assert manual.payload["details"]["reason"] == "server_service redeployed"
        assert manual.payload["details"]["source"] == "cli"
        assert manual.payload["details"]["row_id"] == rid

    async def test_missing_row_returns_false_and_skips_audit(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()

        emit_calls: list[str] = []

        async def track_emit(action, **kw):
            emit_calls.append(action)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            track_emit,
        )

        ok = await cli_outbox.cmd_outbox_reattempt(row_id=999_999_999)
        assert ok is False

        rows = await _all_rows()
        assert rows == []
        # Никакого manual-audit'а быть не должно — мы даже не дошли до
        # enqueue_audit, и flush не нашёл бы ничего.
        assert emit_calls == []

    async def test_already_unpublished_returns_false(self, monkeypatch):
        audit_outbox_publisher._reset_breaker_state()

        # Свежий row без `published_at` — re_attempt_row отдаст False (noop).
        async with AsyncSessionLocal() as session:
            row = AuditOutbox(
                task_id=None,
                payload={"action": "test.evt"},
            )
            session.add(row)
            await session.commit()
            rid = row.id

        async def noop_emit(action, **kw):
            return None

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            noop_emit,
        )

        ok = await cli_outbox.cmd_outbox_reattempt(row_id=rid)
        assert ok is False

        rows = await _all_rows()
        # Должна остаться только исходная row, manual-audit'а нет.
        assert len(rows) == 1
        assert rows[0].id == rid
        assert rows[0].payload["action"] == "test.evt"
