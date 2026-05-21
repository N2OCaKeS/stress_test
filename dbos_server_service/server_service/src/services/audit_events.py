"""Канонический список audit-событий, которые эмитит server_service.

Отправляется в loging_service на startup через `register_events()`. Любое
новое событие, добавленное сюда, автоматически зарегистрируется при
следующем рестарте.
"""

import logging

import httpx

from src.core.config import get_settings

logger = logging.getLogger("audit")

_SERVICE_NAME = "server_service"

# Каждая запись: action, human description, default_severity (для success;
# failure по правилам в loging_service дефолтится в WARNING/CRITICAL).
SERVICE_EVENTS = [
    # Startup / lifecycle
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    # HTTP middleware
    {"action": "http.access_denied", "description": "HTTP 401/403 response", "default_severity": "CRITICAL"},
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "WARNING"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "CRITICAL"},
    # loging_reader НЕ блокируется (он может быть обычным сотрудником со
    # своим department_id и service_roles в server_service) — описание
    # содержит ровно те две роли, что в BLOCKED_PLATFORM_ROLES.
    {"action": "http.platform_admin_blocked", "description": "Platform admin (account_admin + loging_admin) blocked from server_service business endpoint (§7-8 admin-plane separation; loging_reader is NOT blocked)", "default_severity": "WARNING"},
    # Servers — CRUD
    {"action": "server.create", "description": "Server record created", "default_severity": "INFO"},
    {"action": "server.view", "description": "Server record viewed (emitted on denied: cross-dept / nonexistent)", "default_severity": "INFO"},
    {"action": "server.update", "description": "Server record updated", "default_severity": "INFO"},
    {"action": "server.delete", "description": "Server record deleted", "default_severity": "CRITICAL"},
    # Power-cycle (IPMI/iDRAC/iLO/Redfish) — dispatch to server_worker
    {"action": "server.power_on", "description": "Server power-on dispatched to worker", "default_severity": "WARNING"},
    {"action": "server.power_off", "description": "Server power-off dispatched to worker", "default_severity": "WARNING"},
    {"action": "server.power_reboot", "description": "Server reboot dispatched to worker", "default_severity": "WARNING"},
    # Live BMC power-probe — dispatched в worker (power.status task).
    {"action": "server.power_status", "description": "Server power state live-probed via worker (Redfish/ipmitool)", "default_severity": "INFO"},
    # Sensitive: показ расшифрованных секретов (internal endpoints для worker'а)
    {"action": "ipmi_controller.view_credentials", "description": "Decrypted IPMI credentials revealed (internal)", "default_severity": "WARNING"},
    {"action": "ipmi_controller.credentials_revealed", "description": "Decrypted IPMI credentials revealed to user (login + base64 password) via /reveal-credentials", "default_severity": "WARNING"},
    {"action": "server_account.view_password", "description": "Decrypted server account password revealed (internal)", "default_severity": "WARNING"},
    {"action": "server_account.password_revealed", "description": "Decrypted server account password revealed to user (base64) via /reveal-password", "default_severity": "WARNING"},
    {"action": "server_account.rotate_password", "description": "Server account password rotated", "default_severity": "CRITICAL"},
    {"action": "internal.dept_header_missing", "description": "Internal endpoint called without X-Target-Department-Id header (soft mode bypassed server lookup)", "default_severity": "WARNING"},
    # Worker → server_service callbacks (write-direction internal API)
    {"action": "server.inventory_received", "description": "Inventory facts received from worker (hardware probe callback)", "default_severity": "INFO"},
    {"action": "ipmi_controller.credentials_rotated_callback", "description": "Worker confirmed IPMI credentials rotation (callback after worker-side encrypt)", "default_severity": "WARNING"},
    # Постепенная фоновая ротация мастер-ключа (зовётся server_worker'ом)
    {"action": "secrets.reencrypt_batch", "description": "Background secret re-encryption batch processed (called by server_worker periodic task)", "default_severity": "INFO"},
    # Server accounts — CRUD (user-facing)
    {"action": "server_account.create", "description": "Server account created", "default_severity": "CRITICAL"},
    {"action": "server_account.view", "description": "Server account viewed (emitted on denied: cross-dept / nonexistent)", "default_severity": "INFO"},
    {"action": "server_account.list", "description": "Server accounts listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "server_account.update", "description": "Server account updated", "default_severity": "INFO"},
    {"action": "server_account.delete", "description": "Server account deleted", "default_severity": "CRITICAL"},
    # Управление матрицей прав
    {"action": "permission.grant", "description": "entity_permission row granted", "default_severity": "CRITICAL"},
    {"action": "permission.revoke", "description": "entity_permission row revoked", "default_severity": "CRITICAL"},
    # IPMI controllers — CRUD (user-facing)
    {"action": "ipmi_controller.create", "description": "IPMI controller registered for a server", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.view", "description": "IPMI controller card viewed (emitted on denied: cross-dept / nonexistent / not registered)", "default_severity": "INFO"},
    {"action": "ipmi_controller.list", "description": "IPMI controllers listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "ipmi_controller.update", "description": "IPMI controller updated (kind/bmc_vendor/endpoint/username)", "default_severity": "INFO"},
    {"action": "ipmi_controller.delete", "description": "IPMI controller deleted", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.rotate_credentials", "description": "IPMI controller password rotated", "default_severity": "CRITICAL"},
    # Disks — CRUD (привязаны к серверу, dept-isolation через сервер-родитель)
    {"action": "disk.create", "description": "Server disk record created", "default_severity": "INFO"},
    {"action": "disk.view", "description": "Server disk viewed (emitted on denied: cross-dept / nonexistent)", "default_severity": "INFO"},
    {"action": "disk.list", "description": "Server disks listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "disk.update", "description": "Server disk updated", "default_severity": "INFO"},
    {"action": "disk.delete", "description": "Server disk deleted", "default_severity": "WARNING"},
    # Installed packages — live SSH-probe через worker (без БД). Единственный
    # action: dispatch'ер `POST /servers/{id}/installed-packages` + worker-task
    # `installed_packages.list` (обе стороны эмитят с одинаковым action-name).
    {"action": "installed_packages.list", "description": "Installed packages live-listed via worker (SSH dpkg-query/rpm -qa)", "default_severity": "INFO"},
    # OS versions — глобальный каталог
    {"action": "os_version.create", "description": "OS version catalog entry created", "default_severity": "INFO"},
    {"action": "os_version.view", "description": "OS version viewed (emitted on denied)", "default_severity": "INFO"},
    {"action": "os_version.list", "description": "OS versions listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "os_version.update", "description": "OS version updated", "default_severity": "INFO"},
    {"action": "os_version.delete", "description": "OS version deleted", "default_severity": "WARNING"},
    # Worker-dispatch endpoints (см. endpoints/worker_dispatch.py).
    {"action": "server.inventory_sync", "description": "Inventory-sync (SSH-probe) dispatched to worker", "default_severity": "INFO"},
    {"action": "server_account.rotate_password_dispatch", "description": "Server account password rotation dispatched to worker (SSH apply + storage round-trip)", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.rotate_dispatch", "description": "IPMI controller password rotation dispatched to worker (currently safety-guarded: worker fails fast until storage round-trip exists)", "default_severity": "CRITICAL"},
    # Busy-lease, OS-sync — пользовательский CRUD над servers.busy_state / os_version_id
    {"action": "server.acquire", "description": "Server acquired (busy_state set to busy) for a test/lease", "default_severity": "INFO"},
    {"action": "server.release", "description": "Server released (busy_state back to free)", "default_severity": "INFO"},
    {"action": "server.update_os_version", "description": "Server os_version_id updated manually (without inventory sync)", "default_severity": "INFO"},
    # Read-only IPMI views (user-facing)
    {"action": "ipmi_controller.view_credentials_meta", "description": "IPMI credentials metadata viewed (no plaintext password)", "default_severity": "INFO"},
    {"action": "server.power_status_cached", "description": "Cached power_state viewed from server row (no live probe)", "default_severity": "INFO"},
    # Inventory probe (lightweight ping vs full inventory.sync)
    {"action": "server.inventory_probe", "description": "Lightweight inventory probe dispatched to worker", "default_severity": "INFO"},
]


def register_events() -> None:
    """POST полного списка событий в loging_service. Вызывается на startup в фоновом thread'е.

    Не блокирует startup — если loging_service недоступен, мы просто
    логгируем WARNING и едем дальше. Сами события всё равно отправятся
    позже через `audit_service.emit()` — registration нужен только чтобы
    loging_service знал про severity defaults для пар (action, status).
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
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
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
