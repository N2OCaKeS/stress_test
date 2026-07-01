"""Тесты record_admin_action: назначение severity из таблицы _DEFAULT_SEVERITY
когда severity не задан, и preservation при явно указанном severity.

Покрывает src/services/event_service.py::record_admin_action.
"""

from datetime import datetime, timezone

import pytest

from src.core.exceptions import AppException
from src.schemas.events import EventCreate
from src.services.event_service import record_admin_action


def _event(**kwargs) -> EventCreate:
    base = dict(
        timestamp=datetime.now(timezone.utc),
        service="loging_service",
        action="logging_rule.create",
        status="success",
        allowed=True,
        actor_type="user",
        actor_id="usr_admin",
    )
    base.update(kwargs)
    return EventCreate(**base)


class TestAdminActionDefaultSeverity:
    def test_severity_none_uses_default_table(self, db):
        """logging_rule.create → CRITICAL из _DEFAULT_SEVERITY."""
        ev = record_admin_action(db, _event(action="logging_rule.create", severity=None))
        assert ev.severity == "CRITICAL"

    def test_severity_none_logging_rule_update_critical(self, db):
        ev = record_admin_action(db, _event(action="logging_rule.update", severity=None))
        assert ev.severity == "CRITICAL"

    def test_severity_none_logging_rule_delete_critical(self, db):
        ev = record_admin_action(db, _event(action="logging_rule.delete", severity=None))
        assert ev.severity == "CRITICAL"

    def test_severity_none_unknown_action_success_info(self, db):
        ev = record_admin_action(db, _event(action="custom.action", severity=None))
        assert ev.severity == "INFO"

    def test_severity_none_unknown_action_failure_warning(self, db):
        ev = record_admin_action(db, _event(
            action="custom.action", status="failure", allowed=False, severity=None,
        ))
        assert ev.severity == "WARNING"

    def test_explicit_severity_preserved(self, db):
        """Явный severity не перезаписывается дефолтом."""
        ev = record_admin_action(db, _event(
            action="logging_rule.create", severity="DEBUG",
        ))
        assert ev.severity == "DEBUG"

    def test_record_admin_action_does_not_apply_rules(self, db):
        """SUPPRESS-правило не должно подавить запись."""
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        from src.services import rule_service
        rule_repo.create(db, RuleCreate(
            name="suppress-rule-create",
            effect="SUPPRESS",
            match_action="logging_rule.create",
        ))
        rule_service.invalidate_cache()
        ev = record_admin_action(db, _event(action="logging_rule.create", severity=None))
        # Возвращён реальный AuditEvent — не None
        assert ev is not None
        assert ev.action == "logging_rule.create"

    def test_record_admin_action_does_not_apply_override_severity(self, db):
        """OVERRIDE_SEVERITY правило не должно изменить severity admin-аудита."""
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        from src.services import rule_service
        rule_repo.create(db, RuleCreate(
            name="override-info",
            effect="OVERRIDE_SEVERITY",
            match_action="logging_rule.create",
            effect_severity="DEBUG",
        ))
        rule_service.invalidate_cache()
        ev = record_admin_action(db, _event(action="logging_rule.create", severity=None))
        # Должно остаться CRITICAL (из _DEFAULT_SEVERITY), не DEBUG из правила
        assert ev.severity == "CRITICAL"


# ── defence-in-depth: service guard ──────────────────────────────────────────


class TestRecordAdminActionServiceGuard:
    """Технический guard от рефакторинга — `record_admin_action` обходит
    `apply_rules`, поэтому payload с чужим `service` превратил бы её в
    универсальный bypass правил."""

    def test_wrong_service_raises_app_exception(self, db):
        """payload.service != 'loging_service' → AppException(500)."""
        with pytest.raises(AppException) as excinfo:
            record_admin_action(db, _event(service="auth_service"))
        assert excinfo.value.error_code == "ADMIN_AUDIT_WRONG_SERVICE"
        assert excinfo.value.http_status == 500
        assert "auth_service" in excinfo.value.message

    def test_empty_service_raises_app_exception(self, db):
        """Пустая/левая строка — тоже отказ."""
        with pytest.raises(AppException):
            record_admin_action(db, _event(service="server_service"))

    def test_correct_service_passes(self, db):
        """Базовый позитивный кейс — service='loging_service' проходит."""
        ev = record_admin_action(db, _event(service="loging_service"))
        assert ev is not None
        assert ev.service == "loging_service"

    def test_wrong_service_does_not_write_event(self, db):
        """Guard должен сработать ДО event_repo.insert — БД не трогается."""
        from sqlalchemy import select
        from src.models.audit_event import AuditEvent
        with pytest.raises(AppException):
            record_admin_action(db, _event(service="auth_service"))
        # Никаких событий не записано
        rows = db.execute(select(AuditEvent)).scalars().all()
        assert rows == []
