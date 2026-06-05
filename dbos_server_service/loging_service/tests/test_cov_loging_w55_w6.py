"""Coverage gaps W55 W6 — loging_service.

Покрывает четыре ветки, не задетые предыдущими тестами:

  1. `_build_retention_sweep_details` — `not snapshot` ветка при ненулевом
     `deleted_count` (concurrent admin-DELETE снёс политики между
     `list_active` и emit'ом, но sweep уже успел почистить часть строк).
  2. `_emit_idempotency_conflict_audit` — exception внутри
     `record_admin_action` (broken DB) маскируется WARNING-логом, ConflictError
     наверх не маскируется.
  3. `AuditOutbox.qsize()` — `queue is None` ветка (до start / после stop),
     должна возвращать 0, а не падать.
  4. `_sanitize_request_id` — пустой sanitized после фильтрации даёт
     `req_<hex>` fallback (unit-уровень, не через middleware).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.core.exceptions import ConflictError
from src.schemas.events import EventCreate
from src.services import event_service


# ── 1. _build_retention_sweep_details: empty snapshot, deleted > 0 ───────────


class TestRetentionSweepDetailsEmptySnapshotNonzeroDeleted:
    """Concurrent admin-DELETE политик между `list_active` и emit'ом:
    snapshot пустой, но sweep уже успел снести строки до того, как политики
    исчезли. `details["policies"]` пустой, min/max ключей нет, deleted_count
    отражает реальный rowcount."""

    def test_empty_snapshot_with_positive_deleted(self):
        from src.main import _build_retention_sweep_details

        details = _build_retention_sweep_details(
            deleted=42,
            snapshot=[],
            run_date_msk="2026-06-05",
        )
        assert details["deleted_count"] == 42
        assert details["policies"] == []
        assert "min_retain_days" not in details
        assert "max_retain_days" not in details

    def test_empty_snapshot_with_deleted_and_cutoff(self):
        from src.main import _build_retention_sweep_details

        cutoff = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
        details = _build_retention_sweep_details(
            deleted=7,
            snapshot=[],
            run_date_msk="2026-06-05",
            cutoff_at=cutoff,
        )
        assert details["deleted_count"] == 7
        assert details["policies"] == []
        assert details["cutoff_at"] == "2026-06-05T12:00:00+00:00"


# ── 2. _emit_idempotency_conflict_audit: record_admin_action exception ──────


class TestEmitIdempotencyConflictAuditRecordFails:
    """`record_admin_action` внутри `_emit_idempotency_conflict_audit` может
    упасть на broken pool / session-factory failure. Внешний try/except
    проглатывает исключение в WARNING — основной 409 ответ клиенту важнее,
    чем self-audit-row."""

    class _NoTxSession:
        """Сессия с `in_transaction() == False` — проходит misuse-guard."""

        def in_transaction(self) -> bool:
            return False

    def test_record_admin_action_failure_swallowed_to_warning(
        self, monkeypatch, caplog
    ):
        session = self._NoTxSession()
        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_id=None,
            actor_type="service",
            status="failure",
            allowed=False,
            idempotency_key="poison-broken-db",
            details={},
        )
        exc = ConflictError(
            error_code="IDEMPOTENCY_KEY_CONFLICT",
            message="poison",
            details={
                "service": payload.service,
                "idempotency_key": payload.idempotency_key,
            },
        )

        # `record_admin_action` падает на broken-DB — RuntimeError изнутри.
        def fake_record_admin_action(db, audit_payload, *, commit=True):
            raise RuntimeError("connection lost mid-commit")

        monkeypatch.setattr(
            event_service, "record_admin_action", fake_record_admin_action
        )

        with caplog.at_level(logging.WARNING, logger="src.services.event_service"):
            # Ключевое: НЕ должно подняться наружу.
            event_service._emit_idempotency_conflict_audit(session, payload, exc)

        warnings = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "self-audit for IDEMPOTENCY_KEY_CONFLICT failed" in m for m in warnings
        ), f"ожидали WARNING про self-audit failure, получили: {warnings!r}"


# ── 3. AuditOutbox.qsize() при queue is None ─────────────────────────────────


class TestAuditOutboxQsizeBeforeStart:
    """`qsize()` до `start()` / после `stop()` — `self._queue is None`,
    функция возвращает 0 вместо `AttributeError`. Без unit'а ветка ловилась
    только через интеграционные тесты shutdown'а."""

    def test_qsize_returns_zero_when_queue_none(self):
        from src.services.audit_outbox import AuditOutbox

        outbox = AuditOutbox(
            max_size=10,
            batch_size=5,
            poll_interval_seconds=0.1,
            session_factory=lambda: None,  # type: ignore[arg-type]
            writer=lambda db, env: None,
            bump_failure=lambda: 0,
        )
        # `start()` НЕ вызывался — `_queue` остаётся None.
        assert outbox._queue is None
        assert outbox.qsize() == 0


# ── 4. _sanitize_request_id — unit, пустой sanitized → fallback ─────────────


class TestSanitizeRequestIdUnit:
    """`_sanitize_request_id` как top-level helper: пустой и whitespace-only
    input, charset-фильтр, длинные строки. До этого ловилось implicitly через
    middleware, но unit-уровень покрывает edge-cases без HTTP-стенда."""

    def test_none_input_generates_fallback(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id(None)
        assert result.startswith("req_")
        assert len(result) > len("req_")

    def test_empty_string_generates_fallback(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id("")
        assert result.startswith("req_")

    def test_whitespace_only_generates_fallback(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id("   ")
        assert result.startswith("req_")

    def test_charset_filter_drops_out_of_range(self):
        from src.main import _sanitize_request_id

        # Только `;`, `=`, `(`, `)` — всё выбрасывается; sanitized пустой
        # → fallback.
        result = _sanitize_request_id(";=()")
        assert result.startswith("req_")

    def test_charset_filter_keeps_allowed_chars(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id("req_abc.123-XYZ_456")
        assert result == "req_abc.123-XYZ_456"

    def test_crlf_nul_stripped(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id("req\r\nid\x00xx")
        # CR/LF/NUL выкинуты, остальное прошло.
        assert "\r" not in result
        assert "\n" not in result
        assert "\x00" not in result
        assert result == "reqidxx"

    def test_long_input_capped_at_64(self):
        from src.main import _sanitize_request_id

        result = _sanitize_request_id("a" * 500)
        assert len(result) == 64


# ── 5. record_admin_action: invariant-log пишет basename, не full path ──────


class TestRecordAdminActionInvariantLogPath:
    """P4 SEC: `record_admin_action` bypass-guard логирует caller, но без
    абсолютного пути. Внешний log-aggregator не должен видеть внутреннюю
    раскладку проекта."""

    def test_invariant_violation_logs_basename_only(self, monkeypatch, caplog):
        from src.services.event_service import record_admin_action

        bad_payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="auth_service",  # !!! не loging_service → invariant fires
            action="user.login",
            actor_id=None,
            actor_type="service",
            status="success",
            allowed=True,
            details={},
        )

        with caplog.at_level(logging.ERROR, logger="src.services.event_service"):
            with pytest.raises(Exception):
                # Падает с AppException(500) — это ожидаемо.
                record_admin_action(MagicMock(), bad_payload, commit=False)

        errors = [
            r.message for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert errors, "ожидали ERROR-лог про invariant violation"
        # Лог не должен содержать абсолютного пути проекта.
        joined = "\n".join(errors)
        assert "/home/" not in joined
        assert "/src/services/" not in joined
        # Зато basename файла должен быть.
        assert "event_service.py" in joined or "test_" in joined or ".py" in joined
