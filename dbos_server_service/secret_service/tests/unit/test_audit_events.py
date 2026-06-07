"""Каталог `services/audit_events.py` — coverage и форма action'ов."""

from __future__ import annotations

import re

import pytest

from src.services.audit_events import SERVICE_EVENTS, _DEFAULT_SEVERITY, default_severity


# Перебиваем `tests/unit/conftest.py::_create_schema` — БД не трогаем.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_VALID_SEVERITIES = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def test_service_events_action_naming_regex():
    """Все action'ы — `<object>.<verb>` lowercase, без пробелов."""
    for entry in SERVICE_EVENTS:
        action = entry["action"]
        assert _ACTION_RE.match(action), f"Невалидный action: {action!r}"


def test_service_events_no_duplicates():
    actions = [entry["action"] for entry in SERVICE_EVENTS]
    assert len(actions) == len(set(actions)), f"Дубли в SERVICE_EVENTS: {actions}"


def test_service_events_severity_in_canonical_set():
    for entry in SERVICE_EVENTS:
        sev = entry["default_severity"]
        assert sev in _VALID_SEVERITIES, f"{entry['action']!r}: severity {sev!r} не в каноне"


def test_service_events_have_description():
    for entry in SERVICE_EVENTS:
        assert entry.get("description"), f"{entry['action']!r}: пустой description"


def test_default_severity_keys_only_reference_registered_actions():
    registered = {e["action"] for e in SERVICE_EVENTS}
    unknown = [
        action for (action, _status) in _DEFAULT_SEVERITY.keys() if action not in registered
    ]
    assert not unknown, f"_DEFAULT_SEVERITY содержит action'ы вне SERVICE_EVENTS: {unknown}"


def test_default_severity_no_duplicate_pairs():
    keys = list(_DEFAULT_SEVERITY.keys())
    assert len(keys) == len(set(keys)), "Дублирующиеся (action, status) пары"


def test_default_severity_values_in_canonical_set():
    for (action, status), sev in _DEFAULT_SEVERITY.items():
        assert sev in _VALID_SEVERITIES, f"({action},{status}) → {sev!r} не в каноне"


def test_required_success_actions_have_severity():
    """README/AUDIT_EVENTS перечисляет минимум этих success-action'ов."""
    required_success = {
        "tokens.create",
        "tokens.update",
        "tokens.delete",
        "tokens.admin_override_delete",
        "tokens.revealed",
        "tokens.revealed_throttled",
        "tokens.dept_grant_added",
        "tokens.dept_grant_revoked",
        "tokens.dept_revoke_cascade",
        "tokens.dept_recipient_cascade",
        "tokens.role_acl_added",
        "tokens.role_acl_revoked",
        "tokens.owner_user_deleted_block",
        "tokens.owner_dept_deleted_block",
        "tokens.transfer_ownership",
        "tokens.recover",
    }
    missing = [a for a in required_success if default_severity(a, "success") is None]
    assert not missing, f"_DEFAULT_SEVERITY missing success severity for: {missing}"


def test_failure_axis_for_main_actions():
    """Каждая основная CRUD/sensitive action имеет failure severity = ERROR/CRITICAL."""
    failure_required = [
        "tokens.create",
        "tokens.update",
        "tokens.delete",
        "tokens.admin_override_delete",
        "tokens.revealed",
        "tokens.dept_grant_added",
        "tokens.dept_grant_revoked",
        "tokens.dept_revoke_cascade",
        "tokens.dept_recipient_cascade",
        "tokens.transfer_ownership",
    ]
    for action in failure_required:
        sev = default_severity(action, "failure")
        assert sev in {"ERROR", "CRITICAL"}, f"{action}/failure → {sev!r} ожидалось ERROR/CRITICAL"


def test_http_events_present():
    actions = {e["action"] for e in SERVICE_EVENTS}
    assert {"http.client_error", "http.server_error", "http.unauthorized"} <= actions


def test_default_severity_lookup_returns_none_for_unknown():
    assert default_severity("nope.nope", "success") is None
