"""Каталог audit-событий, которые эмитит secret_service.

Регистрируется в loging_service на startup через `register_events()`. Новое
событие, добавленное сюда, поедет в loging_service при следующем рестарте.
Severity дефолты — для success-ветки; failure-ось дополняется ниже в
`_DEFAULT_SEVERITY` и эскалируется до ERROR/CRITICAL.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("audit")

# Полный список зарегистрированных action'ов сервиса. На каждый объект
# `(action, default_severity)` для success-ветки. failure-ось живёт в
# `_DEFAULT_SEVERITY` ниже.
SERVICE_EVENTS = [
    # Lifecycle
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    # HTTP middleware
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "INFO"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "ERROR"},
    {"action": "http.unauthorized", "description": "HTTP 401/403 response", "default_severity": "WARNING"},
    # Tokens / credentials — CRUD
    {"action": "tokens.create", "description": "Credential created", "default_severity": "INFO"},
    {"action": "tokens.update", "description": "Credential updated (name/login/secret)", "default_severity": "INFO"},
    {"action": "tokens.delete", "description": "Credential deleted by owner / dep_admin", "default_severity": "WARNING"},
    {"action": "tokens.admin_override_delete", "description": "Credential deleted by secret_service admin within own department via admin override (requires reason)", "default_severity": "CRITICAL"},
    # Reveal + throttle
    {"action": "tokens.revealed", "description": "Decrypted secret revealed to user (first reveal in 5-min window)", "default_severity": "CRITICAL"},
    {"action": "tokens.revealed_throttled", "description": "Subsequent reveal within 5-min window (INFO trace for noisy UI-polling)", "default_severity": "INFO"},
    {"action": "tokens.revealed_blocked_by_validity", "description": "Reveal denied because current time is outside [valid_from, valid_to] window (410 SECRET_NOT_YET_VALID / SECRET_EXPIRED)", "default_severity": "INFO"},
    # Dept-grants (cross_department flow)
    {"action": "tokens.dept_grant_added", "description": "DeptGrant added by owner dep_admin or secret_service admin of the owning department", "default_severity": "CRITICAL"},
    {"action": "tokens.dept_grant_revoked", "description": "DeptGrant revoked (cascades RoleACL for recipient_dept)", "default_severity": "CRITICAL"},
    {"action": "tokens.dept_revoke_cascade", "description": "Cascade revoke of DeptGrants and RoleACLs caused by revoke department_service_access", "default_severity": "CRITICAL"},
    {"action": "tokens.dept_recipient_cascade", "description": "Cascade DeptGrant + RoleACL deletion triggered by delete_dept(recipient)", "default_severity": "CRITICAL"},
    # Role ACL
    {"action": "tokens.role_acl_added", "description": "RoleACL added (per-credential role grant within a department)", "default_severity": "INFO"},
    {"action": "tokens.role_acl_revoked", "description": "RoleACL revoked", "default_severity": "INFO"},
    # Owner deleted lifecycle
    {"action": "tokens.owner_user_deleted_block", "description": "Credential auto-blocked because owner user was deleted (grace window starts)", "default_severity": "WARNING"},
    {"action": "tokens.owner_dept_deleted_block", "description": "Credential auto-blocked because owner department was deleted (grace window starts)", "default_severity": "WARNING"},
    # Ownership recovery
    {"action": "tokens.transfer_ownership", "description": "Credential ownership transferred (secret_service admin of owning department, or account_admin for cross-dep transfer after owner_dept deletion)", "default_severity": "CRITICAL"},
    {"action": "tokens.recover", "description": "Blocked credential recovered (status returned to active within 30-day window)", "default_severity": "WARNING"},
    # Authorization
    {"action": "tokens.access_denied", "description": "Reader/operator attempted action without permission (no RoleACL or wrong scope)", "default_severity": "INFO"},
    {"action": "tokens.lockout_triggered", "description": "Per-actor lockout activated after repeated denied access attempts (brute-force defense)", "default_severity": "WARNING"},
    # Re-encrypt outbox (proactive key rotation)
    {"action": "secrets.reencrypt_seed", "description": "Reencrypt-outbox seeded with pending rows after master-key rotation", "default_severity": "INFO"},
    {"action": "secrets.reencrypt_process", "description": "Reencrypt-outbox batch processed (decrypt → encrypt under active key)", "default_severity": "INFO"},
]

# Дефолтные severity для пары (action, status). loging_service применяет это
# при отсутствии явной rule'ы. failure-ось — escalation вверх. Все ключи
# должны попадать в `SERVICE_EVENTS`; coverage-тест это проверяет.
_DEFAULT_SEVERITY: dict[tuple[str, str], str] = {
    # Success ветка зеркалит SERVICE_EVENTS.default_severity
    ("service.started", "success"): "INFO",
    ("http.client_error", "failure"): "INFO",
    ("http.server_error", "failure"): "ERROR",
    ("http.unauthorized", "failure"): "WARNING",
    ("tokens.create", "success"): "INFO",
    ("tokens.update", "success"): "INFO",
    ("tokens.delete", "success"): "WARNING",
    ("tokens.admin_override_delete", "success"): "CRITICAL",
    ("tokens.revealed", "success"): "CRITICAL",
    ("tokens.revealed_throttled", "success"): "INFO",
    ("tokens.revealed_blocked_by_validity", "failure"): "INFO",
    ("tokens.dept_grant_added", "success"): "CRITICAL",
    ("tokens.dept_grant_revoked", "success"): "CRITICAL",
    ("tokens.dept_revoke_cascade", "success"): "CRITICAL",
    ("tokens.dept_recipient_cascade", "success"): "CRITICAL",
    ("tokens.role_acl_added", "success"): "INFO",
    ("tokens.role_acl_revoked", "success"): "INFO",
    ("tokens.owner_user_deleted_block", "success"): "WARNING",
    ("tokens.owner_dept_deleted_block", "success"): "WARNING",
    ("tokens.transfer_ownership", "success"): "CRITICAL",
    ("tokens.recover", "success"): "WARNING",
    ("tokens.access_denied", "failure"): "INFO",
    ("tokens.lockout_triggered", "success"): "WARNING",
    ("secrets.reencrypt_seed", "success"): "INFO",
    ("secrets.reencrypt_process", "success"): "INFO",
    ("secrets.reencrypt_process", "failure"): "ERROR",
    # Failure-ось: эскалация вверх. CRUD-операции — ERROR; высоко-чувствительные
    # (reveal / transfer / cross-dep grants) — CRITICAL; служебные — WARNING.
    ("tokens.create", "failure"): "ERROR",
    ("tokens.update", "failure"): "ERROR",
    ("tokens.delete", "failure"): "ERROR",
    ("tokens.admin_override_delete", "failure"): "CRITICAL",
    ("tokens.revealed", "failure"): "CRITICAL",
    ("tokens.dept_grant_added", "failure"): "CRITICAL",
    ("tokens.dept_grant_revoked", "failure"): "CRITICAL",
    ("tokens.dept_revoke_cascade", "failure"): "CRITICAL",
    ("tokens.dept_recipient_cascade", "failure"): "CRITICAL",
    ("tokens.role_acl_added", "failure"): "ERROR",
    ("tokens.role_acl_revoked", "failure"): "ERROR",
    ("tokens.transfer_ownership", "failure"): "CRITICAL",
    ("tokens.recover", "failure"): "ERROR",
}


def default_severity(action: str, status: str) -> str | None:
    """Подсказка severity для пары (action, status). None — нет дефолта."""
    return _DEFAULT_SEVERITY.get((action, status))


def register_events() -> None:
    """POST полного списка событий в loging_service. Вызывается на startup.

    Не блокирует startup — при недоступности loging_service логируем WARNING.
    Сами события полетят позже через `audit_service.emit()`. Регистрация нужна
    только для того, чтобы loging_service знал про severity defaults.
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not logging_url or not api_key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return
    try:
        resp = httpx.post(
            f"{logging_url}/api/logging/v1/services/{_SERVICE_NAME}/events",
            json={"events": SERVICE_EVENTS},
            headers={**bearer_header(api_key), "X-Service-Identity": "secret_service"},
            timeout=getattr(settings, "register_events_timeout_seconds", 2.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "audit: registered %d events (added=%d updated=%d)",
                data.get("total"), data.get("added"), data.get("updated"),
            )
        else:
            logger.warning("audit: event registration failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("audit: event registration error: %s", exc)
