"""Тесты durable audit-outbox'а: staging, дренаж, backoff, DLQ, cleanup.

Ключевой инвариант, ради которого outbox и появился: недоступный
loging_service больше не съедает событие. `emit()` в сеть вообще не ходит
— он пишет строку в `audit_outbox`, а доставкой занимается
`audit_outbox_publisher`. Стиль повторяет `server_worker/tests/
test_audit_outbox.py`: сетевой путь (`audit_service.deliver`) подменяется
рекордером, состояние строки проверяется прямо в БД.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from src.models import AuditOutbox
from src.services import audit_context, audit_outbox, audit_outbox_publisher, audit_service
from src.services.audit_service import AuditDeliveryError
from tests.conftest import auth_hdr as _hdr


class _LoggingSettings:
    """Минимальный Settings: только то, что читают audit_service и publisher."""

    logging_service_url = "http://loging-service"
    logging_service_api_key = "dbos_svc_test_key"
    audit_outbox_batch_size = 20
    # Низкий cap — чтобы attempts_cap-ветка проверялась без 50 итераций.
    audit_outbox_max_publish_attempts = 3
    audit_outbox_retention_hours = 24


@pytest.fixture
def outbox_enabled(monkeypatch):
    """Включить durable-путь аудита и подчистить таблицу после теста."""
    settings = _LoggingSettings()
    monkeypatch.setattr(audit_service, "get_settings", lambda: settings)
    monkeypatch.setattr(audit_outbox_publisher, "get_settings", lambda: settings)
    audit_outbox_publisher._reset_state_for_tests()
    audit_outbox._reset_counters_for_tests()
    yield settings
    audit_outbox_publisher._reset_state_for_tests()
    audit_outbox._reset_counters_for_tests()


@pytest.fixture(autouse=True)
async def _clean_outbox():
    """`audit_outbox` целиком в владении этого домена — чистим безусловно."""
    yield
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditOutbox))
        await db.commit()


async def _rows() -> list[AuditOutbox]:
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AuditOutbox).order_by(AuditOutbox.id.asc()))
        return list(result.scalars().all())


async def _insert_row(**kwargs) -> int:
    """Положить строку в outbox напрямую, минуя emit."""
    from src.db.session import AsyncSessionLocal

    payload = kwargs.pop("payload", {"action": "permission.grant", "status": "success"})
    async with AsyncSessionLocal() as db:
        row = AuditOutbox(action=payload.get("action"), payload=payload, **kwargs)
        db.add(row)
        await db.commit()
        return row.id


class _Recorder:
    """Подмена `audit_service.deliver`: считает вызовы, умеет падать."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error


# ── Staging: событие переживает недоступный loging_service ────────────────────

class TestStaging:
    async def test_emit_lands_in_outbox_not_in_the_network(self, outbox_enabled, monkeypatch):
        """Основной инвариант: emit не ходит в сеть, событие оказывается в БД."""
        deliver = _Recorder()
        monkeypatch.setattr(audit_service, "deliver", deliver)

        token = audit_outbox.begin_scope()
        audit_service.emit(
            "permission.grant",
            actor_id="usr_seed",
            target_id="prm_x",
            target_type="entity_permission",
            details={"role": "custom", "action": "view"},
        )
        written = await audit_outbox.flush_scope(token)

        assert written == 1
        assert deliver.calls == []  # никакого HTTP на emit'е
        rows = await _rows()
        assert len(rows) == 1
        assert rows[0].action == "permission.grant"
        assert rows[0].published_at is None
        assert rows[0].attempts == 0
        assert rows[0].payload["severity"] == "CRITICAL"
        assert rows[0].payload["actor_id"] == "usr_seed"

    async def test_emit_outside_scope_persists_detached(self, outbox_enabled, monkeypatch):
        """Событие из фонового цикла (нет scope'а) тоже доезжает до таблицы."""
        monkeypatch.setattr(audit_service, "deliver", _Recorder())
        assert audit_outbox.current_scope() is None

        audit_service.emit("service.started")
        # Запись идёт отдельной task'ой — дождаться её.
        for _ in range(50):
            if await _rows():
                break
            await asyncio.sleep(0.02)

        rows = await _rows()
        assert [r.action for r in rows] == ["service.started"]

    async def test_non_serializable_details_survive(self, outbox_enabled, monkeypatch):
        """Битый на JSON `details` не рушит запись остального буфера."""
        monkeypatch.setattr(audit_service, "deliver", _Recorder())

        token = audit_outbox.begin_scope()
        audit_service.emit(
            "global_variable.update",
            details={"when": datetime(2026, 9, 18, tzinfo=timezone.utc)},
        )
        audit_service.emit("global_variable.delete", details={"code": "rc"})
        written = await audit_outbox.flush_scope(token)

        assert written == 2
        rows = await _rows()
        assert [r.action for r in rows] == ["global_variable.update", "global_variable.delete"]
        assert "2026-09-18" in rows[0].payload["details"]["when"]

    async def test_staging_disabled_without_logging_settings(self, monkeypatch):
        """Без LOGGING_SERVICE_URL в outbox не пишем — дренажа всё равно нет."""
        token = audit_outbox.begin_scope()
        audit_service.emit("permission.revoke")
        written = await audit_outbox.flush_scope(token)

        assert written == 0
        assert await _rows() == []

    async def test_scope_closed_after_flush_falls_back_to_detached(self, outbox_enabled, monkeypatch):
        """Фоновая task'а, эмитящая после flush'а, не пишет в мёртвый буфер."""
        monkeypatch.setattr(audit_service, "deliver", _Recorder())
        token = audit_outbox.begin_scope()
        scope = audit_outbox.current_scope()
        await audit_outbox.flush_scope()
        assert scope is not None and scope.closed

        # Тот же контекст, буфер уже закрыт — событие уходит отдельной task'ой.
        audit_service.emit("statistics_recalc.completed")
        for _ in range(50):
            if await _rows():
                break
            await asyncio.sleep(0.02)
        audit_outbox.reset_scope(token)

        assert [r.action for r in await _rows()] == ["statistics_recalc.completed"]

    async def test_failed_persist_is_counted_not_raised(self, outbox_enabled):
        """Сбой записи в БД считается в `/ready`-счётчик, но наружу не выходит."""
        before = audit_outbox.get_not_persisted_total()

        # `object()` не кладётся в JSONB — INSERT падает внутри persist.
        written = await audit_outbox.persist([{"action": "broken", "details": object()}])

        assert written == 0
        assert audit_outbox.get_not_persisted_total() == before + 1
        assert await _rows() == []


# ── Drain: доставка, ретраи, backoff ─────────────────────────────────────────

class TestDrain:
    async def test_pending_row_is_delivered(self, outbox_enabled, monkeypatch):
        deliver = _Recorder()
        monkeypatch.setattr(audit_service, "deliver", deliver)
        await _insert_row()

        published = await audit_outbox_publisher.flush_outbox_once()

        assert published == 1
        assert len(deliver.calls) == 1
        assert deliver.calls[0]["action"] == "permission.grant"
        rows = await _rows()
        assert rows[0].published_at is not None
        assert rows[0].next_retry_at is None

    async def test_outage_keeps_row_and_recovery_delivers_it(self, outbox_enabled, monkeypatch):
        """loging лежит → строка на месте с backoff'ом; вернулся → доехала."""
        failing = _Recorder(error=AuditDeliveryError("connect timeout"))
        monkeypatch.setattr(audit_service, "deliver", failing)
        row_id = await _insert_row()

        assert await audit_outbox_publisher.flush_outbox_once() == 0
        rows = await _rows()
        assert rows[0].published_at is None, "событие не потеряно"
        assert rows[0].attempts == 1
        assert rows[0].last_error == "connect timeout"
        assert rows[0].next_retry_at is not None
        assert rows[0].next_retry_at > datetime.now(timezone.utc)

        # Тот же проход больше эту строку не берёт (backoff), и деливер не зовут.
        failing.calls.clear()
        assert await audit_outbox_publisher.flush_outbox_once() == 0
        assert failing.calls == []

        # Время вышло (эмулируем сдвигом next_retry_at), сервис вернулся.
        await _set_retry_at(row_id, datetime.now(timezone.utc) - timedelta(seconds=1))
        ok = _Recorder()
        monkeypatch.setattr(audit_service, "deliver", ok)

        assert await audit_outbox_publisher.flush_outbox_once() == 1
        rows = await _rows()
        assert rows[0].published_at is not None
        assert len(ok.calls) == 1

    async def test_backoff_grows_with_attempts(self, outbox_enabled, monkeypatch):
        monkeypatch.setattr(audit_service, "deliver", _Recorder(error=AuditDeliveryError("5xx")))
        row_id = await _insert_row(attempts=1)

        await audit_outbox_publisher.flush_outbox_once()
        rows = await _rows()
        # attempts=2 → задержка 4s (2^2), с запасом на исполнение теста.
        delta = rows[0].next_retry_at - datetime.now(timezone.utc)
        assert rows[0].attempts == 2
        assert timedelta(seconds=2) < delta <= timedelta(seconds=4)
        assert row_id == rows[0].id

    async def test_retry_after_raises_the_backoff_floor(self, outbox_enabled, monkeypatch):
        """429 с Retry-After: ждём не меньше, чем попросил loging_service."""
        error = AuditDeliveryError("429", status_code=429, retry_after=25.0)
        monkeypatch.setattr(audit_service, "deliver", _Recorder(error=error))
        await _insert_row()

        await audit_outbox_publisher.flush_outbox_once()
        rows = await _rows()
        assert rows[0].published_at is None
        assert rows[0].next_retry_at - datetime.now(timezone.utc) > timedelta(seconds=20)

    async def test_batch_stops_hammering_one_broken_row(self, outbox_enabled, monkeypatch):
        """Нерабочая строка не съедает весь лимит прохода."""
        deliver = _Recorder(error=AuditDeliveryError("5xx"))
        monkeypatch.setattr(audit_service, "deliver", deliver)
        await _insert_row(payload={"action": "permission.grant"})
        await _insert_row(payload={"action": "permission.revoke"})

        await audit_outbox_publisher.flush_outbox_once(limit=5)

        # Ровно по одной попытке на каждую строку, а не 5 на первую.
        assert [c["action"] for c in deliver.calls] == [
            "permission.grant", "permission.revoke",
        ]

    async def test_delivery_order_is_chronological(self, outbox_enabled, monkeypatch):
        deliver = _Recorder()
        monkeypatch.setattr(audit_service, "deliver", deliver)
        await _insert_row(payload={"action": "first"})
        await _insert_row(payload={"action": "second"})
        await _insert_row(payload={"action": "third"})

        assert await audit_outbox_publisher.flush_outbox_once() == 3
        assert [c["action"] for c in deliver.calls] == ["first", "second", "third"]


# ── DLQ ──────────────────────────────────────────────────────────────────────

class TestDlq:
    async def test_permanent_4xx_goes_to_dlq(self, outbox_enabled, monkeypatch):
        error = AuditDeliveryError("422 schema", status_code=422, permanent=True)
        monkeypatch.setattr(audit_service, "deliver", _Recorder(error=error))
        await _insert_row()

        assert await audit_outbox_publisher.flush_outbox_once() == 0
        rows = await _rows()
        assert rows[0].published_at is not None, "строка ушла из выборки"
        assert rows[0].last_error.startswith("[DLQ:permanent_4xx]")
        assert audit_outbox_publisher.get_dlq_total() == 1

    async def test_missing_action_goes_to_dlq(self, outbox_enabled, monkeypatch):
        deliver = _Recorder()
        monkeypatch.setattr(audit_service, "deliver", deliver)
        await _insert_row(payload={"status": "success"})

        assert await audit_outbox_publisher.flush_outbox_once() == 0
        rows = await _rows()
        assert deliver.calls == [], "битый payload в сеть не уходит"
        assert rows[0].last_error.startswith("[DLQ:missing_action]")
        assert audit_outbox_publisher.get_dlq_total() == 1

    async def test_attempts_cap_goes_to_dlq(self, outbox_enabled, monkeypatch):
        monkeypatch.setattr(audit_service, "deliver", _Recorder(error=AuditDeliveryError("5xx")))
        # cap = 3 (см. _LoggingSettings); attempts станет 3 на этой попытке.
        await _insert_row(attempts=2)

        assert await audit_outbox_publisher.flush_outbox_once() == 0
        rows = await _rows()
        assert rows[0].attempts == 3
        assert rows[0].published_at is not None
        assert rows[0].last_error.startswith("[DLQ:attempts_cap]")
        assert audit_outbox_publisher.get_dlq_total() == 1

    async def test_programmatic_error_is_retried_not_dropped(self, outbox_enabled, monkeypatch):
        """Наш баг (не вина loging) — строка остаётся в очереди."""
        async def boom(payload):
            raise ValueError("bug in payload handling")

        monkeypatch.setattr(audit_service, "deliver", boom)
        await _insert_row()

        assert await audit_outbox_publisher.flush_outbox_once() == 0
        rows = await _rows()
        assert rows[0].published_at is None
        assert rows[0].attempts == 1
        assert "ValueError" in rows[0].last_error


# ── Cleanup ──────────────────────────────────────────────────────────────────

class TestCleanup:
    async def test_cleanup_removes_delivered_keeps_pending(self, outbox_enabled):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        await _insert_row(payload={"action": "delivered"}, published_at=old)
        await _insert_row(payload={"action": "pending"})
        await _insert_row(payload={"action": "fresh"}, published_at=datetime.now(timezone.utc))

        deleted = await audit_outbox_publisher.cleanup_published(retention_hours=24)

        assert deleted == 1
        assert sorted(r.action for r in await _rows()) == ["fresh", "pending"]


# ── Сквозной путь запроса ────────────────────────────────────────────────────

class TestRequestScope:
    async def test_denied_request_persists_audit_while_loging_is_down(
        self, outbox_enabled, monkeypatch, client, guest_token,
    ):
        """403 на живом запросе: события лежат в outbox, хотя loging недоступен."""
        monkeypatch.setattr(
            audit_service, "deliver", _Recorder(error=AuditDeliveryError("connect refused")),
        )

        resp = await client.get("/api/testing/v1/permissions", headers=_hdr(guest_token))
        assert resp.status_code == 403

        actions: list[str] = []
        for _ in range(50):
            actions = [r.action for r in await _rows()]
            if "http.access_denied" in actions:
                break
            await asyncio.sleep(0.02)

        assert "http.access_denied" in actions
        rows = await _rows()
        denied = next(r for r in rows if r.action == "http.access_denied")
        assert denied.payload["severity"] == "CRITICAL"
        assert denied.payload["details"]["status_code"] == 403
        assert denied.published_at is None


async def _set_retry_at(row_id: int, when: datetime) -> None:
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = await db.get(AuditOutbox, row_id)
        row.next_retry_at = when
        await db.commit()


# ── Контекст: emit подтягивает request_id/ip из audit_context ────────────────

class TestContextEnrichment:
    async def test_payload_carries_request_context(self, outbox_enabled, monkeypatch):
        monkeypatch.setattr(audit_service, "deliver", _Recorder())
        ctx = audit_context.AuditContext(
            request_id="req_abc123", ip_address="10.0.0.7", user_agent="pytest",
            username="tester", department_id="dep_a",
        )
        ctx_token = audit_context.set_context(ctx)
        try:
            token = audit_outbox.begin_scope()
            audit_service.emit("test_stand.create", target_id="std_2", target_type="test_stand")
            await audit_outbox.flush_scope(token)
        finally:
            audit_context.reset_context(ctx_token)

        rows = await _rows()
        assert rows, "событие должно было доехать до таблицы"
        payload = rows[-1].payload
        assert payload["request_id"] == "req_abc123"
        assert payload["actor_ip"] == "10.0.0.7"
        assert payload["username"] == "tester"
        assert payload["department_id"] == "dep_a"
