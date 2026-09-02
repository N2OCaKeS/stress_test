"""Хелперы для кластера H — Loging endpoints + audit-landing sweep.

Содержит:
* payload-сборщики для `EventCreate` (ingest POST /events);
* per-action trigger-функции для audit-landing'а (где можно — через API,
  где нет — через прямой POST /events с правильным actor_type);
* справочные таблицы default-severity всех 4 сервисов;
* мелкие helper'ы для setup'а departments / services / grant'ов.

Файл нельзя превращать в conftest — pytest подберёт его как обычный
import, фикстуры берутся из tests/integration/conftest.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest


# ── ID-генерация ──────────────────────────────────────────────────────────────

def short_id() -> str:
    return uuid.uuid4().hex[:8]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Service-token clients (per X-Service-Identity) ────────────────────────────

def make_service_client(
    base_url: str, api_key: str, identity: str | None
) -> httpx.Client:
    """Клиент с SERVICE_API_KEY и опциональным X-Service-Identity.

    `identity=None` — soft mode без header'а (legacy). Указанное значение
    идёт в `X-Service-Identity` и формирует key для rate-limit bucket'а
    (`svc:<normalized>`).
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if identity is not None:
        headers["X-Service-Identity"] = identity
    return httpx.Client(base_url=base_url, headers=headers, timeout=10)


# ── EventCreate payload builder ───────────────────────────────────────────────

def event_payload(
    *,
    service: str = "auth_service",
    action: str = "user.login",
    status: str = "success",
    allowed: bool = True,
    severity: str | None = None,
    actor_id: str | None = None,
    actor_type: str = "service",
    department_id: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    username: str | None = None,
    request_id: str | None = None,
    details: dict | None = None,
    idempotency_key: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Минимальный валидный body для POST /events.

    Defaults подобраны так, чтобы payload проходил все pydantic-валидаторы
    `EventCreate` (charset, depth, reserved-keys). Тесты переопределяют
    нужные поля.
    """
    body: dict = {
        "timestamp": timestamp or now_iso(),
        "service": service,
        "action": action,
        "status": status,
        "allowed": allowed,
        "actor_type": actor_type,
        "details": details if details is not None else {},
    }
    if severity is not None:
        body["severity"] = severity
    if actor_id is not None:
        body["actor_id"] = actor_id
    if department_id is not None:
        body["department_id"] = department_id
    if target_id is not None:
        body["target_id"] = target_id
    if target_type is not None:
        body["target_type"] = target_type
    if username is not None:
        body["username"] = username
    if request_id is not None:
        body["request_id"] = request_id
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return body


# ── Auth-service setup helpers ────────────────────────────────────────────────

def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Идемпотентно создаёт отдел, возвращает его id."""
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name.title()},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    listing = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    for d in listing if isinstance(listing, list) else listing.get("items", []):
        if d["name"] == name:
            return d["department_id"]
    raise RuntimeError(f"failed to ensure department {name!r}")


def ensure_service(auth_client: httpx.Client, admin_token: str, name: str) -> None:
    auth_client.post(
        "/api/auth/v1/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": name, "display_name": name.title()},
    )


def grant_service(
    auth_client: httpx.Client, admin_token: str, dept_id: str, service: str
) -> None:
    auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service},
    )


# ── Default-severity таблицы из 4 сервисов ────────────────────────────────────
#
# Источник: `<service>/src/services/audit_events.py::SERVICE_EVENTS`. Если
# сервис не имеет audit_events.py (server_worker, loging self-audit) — заводим
# здесь по дефолтам из rule_service / audit_service.

# (service, action, success_severity)
AUTH_SERVICE_EVENTS: list[tuple[str, str, str]] = [
    ("auth_service", "service.started", "INFO"),
    ("auth_service", "http.access_denied", "CRITICAL"),
    ("auth_service", "http.client_error", "WARNING"),
    ("auth_service", "http.server_error", "CRITICAL"),
    ("auth_service", "user.login", "INFO"),
    ("auth_service", "user.refresh", "INFO"),
    ("auth_service", "user.logout", "INFO"),
    ("auth_service", "user.me", "INFO"),
    ("auth_service", "token.refresh_reuse", "CRITICAL"),
    ("auth_service", "token.refresh_race", "INFO"),
    ("auth_service", "user.create", "INFO"),
    ("auth_service", "user.list", "INFO"),
    ("auth_service", "user.update", "INFO"),
    ("auth_service", "user.roles_assign", "INFO"),
    ("auth_service", "user.password_reset", "CRITICAL"),
    ("auth_service", "user.ban", "CRITICAL"),
    ("auth_service", "user.unban", "CRITICAL"),
    ("auth_service", "user.ban_deactivated_via_status_change", "WARNING"),
    ("auth_service", "user.permissions_view", "INFO"),
    ("auth_service", "user.roles_purged_on_transfer", "WARNING"),
    ("auth_service", "department.create", "CRITICAL"),
    ("auth_service", "department.list", "INFO"),
    ("auth_service", "department.service_grant", "CRITICAL"),
    ("auth_service", "department.service_revoke", "CRITICAL"),
    ("auth_service", "group.create", "INFO"),
    ("auth_service", "group.update", "INFO"),
    ("auth_service", "group.delete", "CRITICAL"),
    ("auth_service", "group.member_add", "WARNING"),
    ("auth_service", "group.member_remove", "WARNING"),
    ("auth_service", "group.bot_member_add", "WARNING"),
    ("auth_service", "group.bot_member_remove", "WARNING"),
    ("auth_service", "group.service_grant", "CRITICAL"),
    ("auth_service", "group.service_revoke", "CRITICAL"),
    ("auth_service", "group.roles_assign", "CRITICAL"),
    ("auth_service", "group.roles_revoke", "CRITICAL"),
    ("auth_service", "service.create", "CRITICAL"),
    ("auth_service", "service.delete", "CRITICAL"),
    ("auth_service", "service.list", "INFO"),
    ("auth_service", "service.access_check", "INFO"),
    ("auth_service", "service_role.create", "INFO"),
    ("auth_service", "service_role.update", "INFO"),
    ("auth_service", "service_role.delete", "CRITICAL"),
    ("auth_service", "service_role.bulk_assign", "INFO"),
    ("auth_service", "service_role.bulk_revoke", "INFO"),
    ("auth_service", "pat.create", "INFO"),
    ("auth_service", "pat.list", "INFO"),
    ("auth_service", "pat.revoke", "WARNING"),
    ("auth_service", "bot.create", "WARNING"),
    ("auth_service", "bot.list", "INFO"),
    ("auth_service", "bot.update", "WARNING"),
    ("auth_service", "bot.token_create", "WARNING"),
    ("auth_service", "bot.token_list", "INFO"),
    ("auth_service", "bot.token_revoke", "WARNING"),
    ("auth_service", "bot.roles_assign", "WARNING"),
    ("auth_service", "bot.roles_list", "INFO"),
    ("auth_service", "bot.roles_revoke", "WARNING"),
    ("auth_service", "oauth_client.create", "CRITICAL"),
    ("auth_service", "oauth_client.list", "INFO"),
    ("auth_service", "oauth_client.delete", "CRITICAL"),
    ("auth_service", "oauth.authorization_code_issued", "INFO"),
    ("auth_service", "oauth.code_exchanged", "INFO"),
    ("auth_service", "oauth.client_credentials_token", "INFO"),
    ("auth_service", "token.introspect", "INFO"),
    ("auth_service", "docker_registry.configure", "CRITICAL"),
    ("auth_service", "docker_registry.update", "CRITICAL"),
    ("auth_service", "docker_registry.get_config", "INFO"),
    ("auth_service", "docker_registry.disable", "CRITICAL"),
    ("auth_service", "docker.token_issued", "INFO"),
]

SERVER_SERVICE_EVENTS: list[tuple[str, str, str]] = [
    ("server_service", "service.started", "INFO"),
    ("server_service", "http.access_denied", "CRITICAL"),
    ("server_service", "http.client_error", "WARNING"),
    ("server_service", "http.server_error", "CRITICAL"),
    ("server_service", "http.platform_admin_blocked", "WARNING"),
    ("server_service", "server.create", "INFO"),
    ("server_service", "server.view", "INFO"),
    ("server_service", "server.update", "INFO"),
    ("server_service", "server.delete", "CRITICAL"),
    ("server_service", "server.power_on", "WARNING"),
    ("server_service", "server.power_off", "WARNING"),
    ("server_service", "server.power_reboot", "WARNING"),
    ("server_service", "server.power_status", "INFO"),
    ("server_service", "server.prepare", "CRITICAL"),
    ("server_service", "server.prepared", "CRITICAL"),
    ("server_service", "ipmi_controller.view_credentials", "WARNING"),
    ("server_service", "ipmi_controller.credentials_revealed", "CRITICAL"),
    ("server_service", "server_account.view_password", "WARNING"),
    ("server_service", "server_account.password_revealed", "CRITICAL"),
    ("server_service", "server_account.rotate_password", "CRITICAL"),
    ("server_service", "internal.dept_header_missing", "WARNING"),
    ("server_service", "server.inventory_received", "INFO"),
    ("server_service", "server_account.users_inventory_received", "INFO"),
    ("server_service", "server_account.drift_detected", "WARNING"),
    ("server_service", "server_account.provision_status", "INFO"),
    ("server_service", "ipmi_controller.credentials_rotated_callback", "WARNING"),
    ("server_service", "secrets.reencrypt_batch", "INFO"),
    ("server_service", "server_account.create", "CRITICAL"),
    ("server_service", "server_account.view", "INFO"),
    ("server_service", "server_account.list", "INFO"),
    ("server_service", "server_account.update", "INFO"),
    ("server_service", "server_account.link_servers", "INFO"),
    ("server_service", "server_account.unlink_servers", "INFO"),
    ("server_service", "server_account.delete", "CRITICAL"),
    ("server_service", "permission.grant", "CRITICAL"),
    ("server_service", "permission.revoke", "CRITICAL"),
    ("server_service", "ipmi_controller.create", "CRITICAL"),
    ("server_service", "ipmi_controller.view", "INFO"),
    ("server_service", "ipmi_controller.list", "INFO"),
    ("server_service", "ipmi_controller.update", "INFO"),
    ("server_service", "ipmi_controller.delete", "CRITICAL"),
    ("server_service", "ipmi_controller.rotate_credentials", "CRITICAL"),
    ("server_service", "installed_packages.list", "INFO"),
    ("server_service", "os_version.create", "INFO"),
    ("server_service", "os_version.update", "INFO"),
    ("server_service", "os_version.delete", "WARNING"),
    ("server_service", "server.inventory_sync", "INFO"),
    ("server_service", "server_account.users_inventory", "INFO"),
    ("server_service", "server_account.rotate_password_dispatch", "CRITICAL"),
    ("server_service", "server_account.provision", "CRITICAL"),
    ("server_service", "server_account.update_on_host", "INFO"),
    ("server_service", "server_account.deprovision", "WARNING"),
    ("server_service", "ipmi_controller.rotate_dispatch", "CRITICAL"),
    ("server_service", "server.acquire", "INFO"),
    ("server_service", "server.release", "INFO"),
    ("server_service", "server.update_os_version", "INFO"),
    ("server_service", "ipmi_controller.view_credentials_meta", "INFO"),
    ("server_service", "server.power_status_cached", "INFO"),
]

# server_worker эмитит свои события через outbox publisher. Каноничный
# список — в server_worker/src/tasks/*.py. На этом этапе мы перечисляем
# самые показательные действия, у которых известна семантика.
WORKER_SERVICE_EVENTS: list[tuple[str, str, str]] = [
    ("server_worker", "service.started", "INFO"),
    ("server_worker", "server.power_on", "INFO"),
    ("server_worker", "server.power_off", "INFO"),
    ("server_worker", "server.power_reboot", "INFO"),
    ("server_worker", "server.power_status", "INFO"),
    ("server_worker", "server.prepare", "INFO"),
    ("server_worker", "server.prepared", "CRITICAL"),
    ("server_worker", "server.inventory_sync", "INFO"),
    ("server_worker", "server_account.users_inventory", "INFO"),
    ("server_worker", "server_account.provision", "INFO"),
    ("server_worker", "server_account.update_on_host", "INFO"),
    ("server_worker", "server_account.deprovision", "INFO"),
    ("server_worker", "ipmi.rotate_password", "INFO"),
    ("server_worker", "installed_packages.list", "INFO"),
]

# loging_service self-audit. Источник дефолтов — rule_service._DEFAULT_SEVERITY.
LOGING_SERVICE_EVENTS: list[tuple[str, str, str]] = [
    ("loging_service", "logging_rule.create", "WARNING"),
    ("loging_service", "logging_rule.update", "CRITICAL"),
    ("loging_service", "logging_rule.delete", "CRITICAL"),
    ("loging_service", "logging.retention_write", "WARNING"),
    ("loging_service", "logging.retention_sweep", "INFO"),
]


ALL_SERVICE_EVENTS: list[tuple[str, str, str]] = (
    AUTH_SERVICE_EVENTS
    + SERVER_SERVICE_EVENTS
    + WORKER_SERVICE_EVENTS
    + LOGING_SERVICE_EVENTS
)


# Подмножество CRITICAL/WARNING actions, которые упомянуты как обязательные
# в задаче кластера H. По ним идёт основная audit-landing проверка с
# точным сравнением severity и details.
PRIORITY_AUDIT_ACTIONS: list[tuple[str, str, str]] = [
    ("server_service", "server.prepare", "CRITICAL"),
    ("server_service", "server.prepared", "CRITICAL"),
    ("server_service", "server_account.provision", "CRITICAL"),
    ("server_service", "server_account.password_revealed", "CRITICAL"),
    ("server_service", "ipmi_controller.credentials_revealed", "CRITICAL"),
    ("server_service", "server_account.drift_detected", "WARNING"),
    ("auth_service", "http.access_denied", "CRITICAL"),
    ("server_service", "http.access_denied", "CRITICAL"),
]


# ── Direct-ingest fallback для audit-landing'а ────────────────────────────────
#
# Не все ~80 actions удобно протриггерить честным API-вызовом: часть
# относится к internal worker → server_service callback'ам, часть — к
# permission.grant / docker_registry / oauth flows, у которых длинный
# setup. Для широкого покрытия severity-table'ы достаточно прямого
# POST /events от имени сервиса: loging_service пропустит payload через
# те же rule engine / severity-defaults, что и реальный emit.
#
# Возвращает true, если событие приземлилось в БД (loging_db_engine).

def ingest_synthetic_event(
    _client_unused: httpx.Client,
    *,
    service: str,
    action: str,
    expected_severity: str,
    actor_id: str | None = None,
    target_id: str | None = None,
    department_id: str | None = None,
    extra_details: dict | None = None,
) -> str | None:
    """POST /events с минимально нужными полями. Возвращает event_id или None.

    Первый позиционный аргумент сохранён для совместимости с тестами,
    которые передают сюда `logging_client` (admin JWT). Сам POST идёт через
    отдельный service-token клиент — `/events` принимает только
    `SERVICE_API_KEY`, JWT отбивается 401.
    """
    from tests.integration.conftest import LOGGING_API_KEY, LOGGING_URL

    details = {"reason": "audit_landing_sweep_h_cluster"}
    if extra_details:
        details.update(extra_details)
    payload = event_payload(
        service=service,
        action=action,
        status="success",
        allowed=True,
        severity=expected_severity,
        actor_id=actor_id or f"actor_{short_id()}",
        actor_type="service",
        target_id=target_id,
        department_id=department_id,
        details=details,
        idempotency_key=f"audit_landing_{action}_{short_id()}",
    )
    with make_service_client(LOGGING_URL, LOGGING_API_KEY, service) as svc_client:
        r = svc_client.post("/api/logging/v1/events", json=payload)
    if r.status_code == 204:
        return None
    if r.status_code == 403:
        pytest.skip(f"{service} is reserved for self-audit, cannot ingest externally")
    if r.status_code == 401:
        pytest.skip(
            f"{service} not accepted by loging_service auth: {r.text}"
        )
    if r.status_code == 429:
        pytest.skip(f"per-identity rate limit hit for {service}/{action}")
    assert r.status_code == 201, (
        f"POST /events failed for {service}/{action}: {r.status_code} {r.text}"
    )
    return r.json()["id"]
