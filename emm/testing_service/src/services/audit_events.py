"""Каталог audit-событий, которые эмитит testing_service.

Регистрируется в loging_service на startup через `register_events()`.
Инфраструктурные события (lifecycle + HTTP middleware) плюс те бизнес-события,
чей домен уже реализован. Остальные (`test.launch`/`test.cancel`/
`stp.status_updated`/...) добавляются вместе с доменом, который их производит.

Имя действия — `<object>.<verb>` (`global_variable.create`), как в остальных
сервисах emm.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("audit")

SERVICE_EVENTS = [
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    {"action": "http.access_denied", "description": "HTTP 401/403 response", "default_severity": "CRITICAL"},
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "WARNING"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "CRITICAL"},
    {"action": "global_variable.create", "description": "Global variable created", "default_severity": "INFO"},
    {"action": "global_variable.update", "description": "Global variable updated", "default_severity": "INFO"},
    {"action": "global_variable.delete", "description": "Global variable deleted", "default_severity": "WARNING"},
    {"action": "test_definition.create", "description": "Test definition created", "default_severity": "INFO"},
    {"action": "test_definition.update", "description": "Test definition updated", "default_severity": "INFO"},
    {"action": "test_definition.delete", "description": "Test definition deleted", "default_severity": "WARNING"},
    {"action": "test_command_arg.create", "description": "Test command arg slot added", "default_severity": "INFO"},
    {"action": "test_command_arg.update", "description": "Test command arg slot updated", "default_severity": "INFO"},
    {"action": "test_command_arg.delete", "description": "Test command arg slot removed", "default_severity": "INFO"},
    {"action": "test_stand.create", "description": "Test stand registered", "default_severity": "INFO"},
    {"action": "test_stand.update", "description": "Test stand updated", "default_severity": "INFO"},
    {"action": "test_stand.delete", "description": "Test stand deleted", "default_severity": "WARNING"},
    {"action": "test_stand.test_credentials_viewed", "description": "Test stand's test-user credentials viewed via server_service proxy", "default_severity": "WARNING"},
    {"action": "department_test_settings.update", "description": "Department test settings upserted", "default_severity": "INFO"},
    {"action": "queue_item.enqueued", "description": "Test queued on a stand", "default_severity": "INFO"},
    {"action": "queue_item.prepare_requested", "description": "prepare-for-test requested from server_service", "default_severity": "INFO"},
    {"action": "queue_item.prepare_start_failed", "description": "Could not start the preparation cycle (acquire/prepare-for-test call failed)", "default_severity": "WARNING"},
    {"action": "queue_item.ready", "description": "prepare-for-test callback succeeded, credentials stashed", "default_severity": "INFO"},
    {"action": "queue_item.prepare_failed", "description": "prepare-for-test callback reported failure", "default_severity": "WARNING"},
    {"action": "queue_item.retry_created", "description": "A retry queue item was created after a failure", "default_severity": "INFO"},
    {"action": "queue_item.claimed", "description": "testing_worker claimed a ready queue item", "default_severity": "INFO"},
    {"action": "queue_item.completed", "description": "testing_worker reported the outcome of an SSH run", "default_severity": "INFO"},
    {"action": "test_log.rotated", "description": "A test log was deleted by the rotation policy (duplicate relaunch or monthly retention)", "default_severity": "WARNING"},
    {"action": "test_run.create", "description": "A fleet-wide test run campaign was created", "default_severity": "INFO"},
    {"action": "test_run.status_changed", "description": "A test run campaign's aggregate status changed after a queue item transition", "default_severity": "INFO"},
    {"action": "stp_test_case.create", "description": "STP test case (Zephyr mirror) created", "default_severity": "INFO"},
    {"action": "stp_test_case.update", "description": "STP test case updated", "default_severity": "INFO"},
    {"action": "stp_test_case.delete", "description": "STP test case deleted", "default_severity": "WARNING"},
    {"action": "stp_test_run.generate", "description": "STP test runs generated in Zephyr for a department/RC/mode/kernel", "default_severity": "INFO"},
    {"action": "stp_cell.auto_updated", "description": "STP cell status updated automatically from a queue item transition", "default_severity": "INFO"},
    {"action": "stp_cell.manual_override", "description": "STP cell status overridden manually (not synced to Zephyr)", "default_severity": "WARNING"},
    {"action": "department_integration_settings.update", "description": "Department Jira/Zephyr/Confluence integration settings upserted", "default_severity": "INFO"},
    {"action": "run_summary_comment.posted", "description": "End-of-run Confluence blog comment posted/updated/skipped/failed for a test run campaign", "default_severity": "INFO"},
    {"action": "department_report_member.create", "description": "Department report member added", "default_severity": "INFO"},
    {"action": "department_report_member.update", "description": "Department report member updated", "default_severity": "INFO"},
    {"action": "department_report_member.delete", "description": "Department report member removed", "default_severity": "WARNING"},
    {"action": "department_activity_report.generate", "description": "Department activity report generation attempted (manual trigger)", "default_severity": "INFO"},
]

_DEFAULT_SEVERITY: dict[tuple[str, str], str] = {
    ("service.started", "success"): "INFO",
    ("http.access_denied", "failure"): "CRITICAL",
    ("http.client_error", "failure"): "WARNING",
    ("http.server_error", "failure"): "CRITICAL",
    ("global_variable.create", "success"): "INFO",
    ("global_variable.create", "failure"): "WARNING",
    ("global_variable.create", "denied"): "WARNING",
    ("global_variable.update", "success"): "INFO",
    ("global_variable.update", "failure"): "WARNING",
    ("global_variable.update", "denied"): "WARNING",
    ("global_variable.delete", "success"): "WARNING",
    ("global_variable.delete", "failure"): "WARNING",
    ("global_variable.delete", "denied"): "WARNING",
    ("test_definition.create", "success"): "INFO",
    ("test_definition.create", "failure"): "WARNING",
    ("test_definition.create", "denied"): "WARNING",
    ("test_definition.update", "success"): "INFO",
    ("test_definition.update", "failure"): "WARNING",
    ("test_definition.update", "denied"): "WARNING",
    ("test_definition.delete", "success"): "WARNING",
    ("test_definition.delete", "failure"): "WARNING",
    ("test_definition.delete", "denied"): "WARNING",
    ("test_command_arg.create", "success"): "INFO",
    ("test_command_arg.create", "failure"): "WARNING",
    ("test_command_arg.create", "denied"): "WARNING",
    ("test_command_arg.update", "success"): "INFO",
    ("test_command_arg.update", "failure"): "WARNING",
    ("test_command_arg.update", "denied"): "WARNING",
    ("test_command_arg.delete", "success"): "INFO",
    ("test_command_arg.delete", "failure"): "WARNING",
    ("test_command_arg.delete", "denied"): "WARNING",
    ("test_stand.create", "success"): "INFO",
    ("test_stand.create", "failure"): "WARNING",
    ("test_stand.create", "denied"): "WARNING",
    ("test_stand.update", "success"): "INFO",
    ("test_stand.update", "failure"): "WARNING",
    ("test_stand.update", "denied"): "WARNING",
    ("test_stand.delete", "success"): "WARNING",
    ("test_stand.delete", "failure"): "WARNING",
    ("test_stand.delete", "denied"): "WARNING",
    ("test_stand.test_credentials_viewed", "success"): "WARNING",
    ("test_stand.test_credentials_viewed", "denied"): "WARNING",
    ("department_test_settings.update", "success"): "INFO",
    ("department_test_settings.update", "denied"): "WARNING",
    ("queue_item.enqueued", "success"): "INFO",
    ("queue_item.prepare_requested", "success"): "INFO",
    ("queue_item.prepare_start_failed", "failure"): "WARNING",
    ("queue_item.ready", "success"): "INFO",
    ("queue_item.prepare_failed", "failure"): "WARNING",
    ("queue_item.retry_created", "success"): "INFO",
    ("queue_item.claimed", "success"): "INFO",
    ("queue_item.completed", "success"): "INFO",
    ("queue_item.completed", "failure"): "WARNING",
    ("test_log.rotated", "success"): "WARNING",
    ("test_run.create", "success"): "INFO",
    ("test_run.create", "denied"): "WARNING",
    ("test_run.status_changed", "success"): "INFO",
    ("stp_test_case.create", "success"): "INFO",
    ("stp_test_case.create", "failure"): "WARNING",
    ("stp_test_case.create", "denied"): "WARNING",
    ("stp_test_case.update", "success"): "INFO",
    ("stp_test_case.update", "failure"): "WARNING",
    ("stp_test_case.update", "denied"): "WARNING",
    ("stp_test_case.delete", "success"): "WARNING",
    ("stp_test_case.delete", "denied"): "WARNING",
    ("stp_test_run.generate", "success"): "INFO",
    ("stp_test_run.generate", "denied"): "WARNING",
    ("stp_cell.auto_updated", "success"): "INFO",
    ("stp_cell.manual_override", "success"): "WARNING",
    ("stp_cell.manual_override", "denied"): "WARNING",
    ("department_integration_settings.update", "success"): "INFO",
    ("department_integration_settings.update", "denied"): "WARNING",
    ("run_summary_comment.posted", "success"): "INFO",
    ("run_summary_comment.posted", "failure"): "WARNING",
    ("department_report_member.create", "success"): "INFO",
    ("department_report_member.create", "denied"): "WARNING",
    ("department_report_member.update", "success"): "INFO",
    ("department_report_member.update", "denied"): "WARNING",
    ("department_report_member.delete", "success"): "WARNING",
    ("department_report_member.delete", "denied"): "WARNING",
    ("department_activity_report.generate", "success"): "INFO",
    ("department_activity_report.generate", "failure"): "WARNING",
    ("department_activity_report.generate", "denied"): "WARNING",
}


def default_severity(action: str, status: str) -> str | None:
    """Подсказка severity для пары (action, status). None — нет дефолта."""
    return _DEFAULT_SEVERITY.get((action, status))


def register_events() -> None:
    """POST полного списка событий в loging_service. Вызывается на startup.

    Не блокирует startup — при недоступности loging_service логируем WARNING.
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
            headers={**bearer_header(api_key), "X-Service-Identity": "testing_service"},
            timeout=2.0,
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
