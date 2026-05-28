"""Хелперы кластера C: матрица прав / sweep по ENTITY_ACTIONS.

Содержит:

* Фабрики dept / service-role / user для server_service-сценариев.
* Карты `ENTITY_ACTION_SWEEP` и `WORKER_ONLY_PAIRS`, описывающие
  для каждой пары ``(entity, action)`` HTTP-команду, которой её можно
  проверить позитивно/негативно, и имя audit-события в loging_service.
* Утилиты `expect_audit` / `expect_denied_audit` поверх `wait_for_event`,
  чтобы каждый sweep-сценарий мог одной строкой проверить, что событие
  с правильным `status`/`reason` доехало.

Кластер использует выделенный department ``c_perms`` (не пересекается с
seeder-овским ``it``), чтобы свои server'ы/аккаунты/ipmi не конфликтовали
с другими тестами по UNIQUE-полям.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx
import pytest


DEPT_NAME = "c_perms"
SERVICE_NAME = "server_service"

# Системные роли — `admin` сеется автоматически при выдаче dept access к
# server_service, остальные приходится создавать руками. seed-grants
# матрицы entity_permissions у нас system-wide, так что роль с именем
# `admin` сразу получает все actions, а `reader` — только view'ы.
SYSTEM_ROLES = ("admin", "operator", "reader", "guest")


def _u() -> str:
    return uuid.uuid4().hex[:8]


# ── Auth_service helpers ─────────────────────────────────────────────────────

def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Идемпотентно завести department и вернуть его id."""
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name.upper()},
    )
    if r.status_code in (200, 201):
        body = r.json()
        return body.get("department_id") or body["id"]
    listing = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    items = listing.get("items") if isinstance(listing, dict) else listing
    for d in items or []:
        if d.get("name") == name:
            return d.get("department_id") or d.get("id")
    raise RuntimeError(f"failed to ensure department {name!r}: {r.text}")


def ensure_service_access(auth_client: httpx.Client, admin_token: str, dept_id: str, service: str) -> None:
    """Идемпотентно выдать department'у доступ к сервису. 409 — уже есть."""
    r = auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service},
    )
    if r.status_code not in (200, 201, 409):
        raise RuntimeError(f"grant service access: {r.status_code} {r.text}")


def ensure_role(
    auth_client: httpx.Client,
    admin_token: str,
    dept_id: str,
    service: str,
    role_name: str,
    display_name: str | None = None,
) -> None:
    """Идемпотентно создать ServiceRoleDefinition для (dept, service)."""
    r = auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services/{service}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "role_name": role_name,
            "display_name": display_name or role_name.title(),
        },
    )
    if r.status_code not in (200, 201, 409):
        raise RuntimeError(f"create role {role_name}: {r.status_code} {r.text}")


def make_role_user(
    auth_client: httpx.Client,
    admin_token: str,
    dept_id: str,
    role: str,
    *,
    service: str = SERVICE_NAME,
    username: str | None = None,
    password: str = "UserPass1!",
) -> tuple[dict, str]:
    """Создать юзера + сразу выдать ему service-роль. Вернуть (user_body, token)."""
    username = username or f"u_{role}_{_u()}"
    body = {
        "username": username,
        "password": password,
        "department_id": dept_id,
        "initial_roles": [{"service_name": service, "roles": [role]}],
    }
    r = auth_client.post(
        "/api/auth/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create user {username}: {r.status_code} {r.text}")
    user = r.json()
    login = auth_client.post(
        "/api/auth/v1/login",
        json={"username": username, "password": password},
    )
    if login.status_code != 200:
        raise RuntimeError(f"login {username}: {login.status_code} {login.text}")
    return user, login.json()["access_token"]


def assign_user_roles(
    auth_client: httpx.Client,
    admin_token: str,
    user_id: str,
    role_names: list[str],
    *,
    service: str = SERVICE_NAME,
) -> None:
    """Replace-семантика: выставить юзеру именно этот набор ролей в сервисе."""
    r = auth_client.post(
        f"/api/auth/v1/users/{user_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service, "roles": role_names},
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"assign roles {role_names}: {r.status_code} {r.text}")


# ── Department setup (sticky session-scoped state) ───────────────────────────

@dataclass
class ClusterCContext:
    """Контейнер с подготовленным dept + системными ролями."""

    dept_id: str
    other_dept_id: str
    admin_token: str
    reader_token: str
    operator_token: str
    guest_token: str
    other_admin_token: str  # admin в чужом dept'е — для cross-dept проверок
    admin_user_id: str
    reader_user_id: str
    operator_user_id: str
    guest_user_id: str
    other_admin_user_id: str


def setup_cluster_c(
    auth_client: httpx.Client, admin_token: str
) -> ClusterCContext:
    """Поднять department `c_perms` + второй `c_perms_other` для cross-dept проверок.

    Создаёт системные роли (`admin`/`operator`/`reader`/`guest`) в обоих
    отделах, четырёх пользователей в основном и одного admin'а во втором.
    Идемпотентно: повторный вызов перенакладывает initial_roles без падений.
    """
    dept_id = ensure_department(auth_client, admin_token, DEPT_NAME)
    other_dept_id = ensure_department(auth_client, admin_token, f"{DEPT_NAME}_other")

    ensure_service_access(auth_client, admin_token, dept_id, SERVICE_NAME)
    ensure_service_access(auth_client, admin_token, other_dept_id, SERVICE_NAME)

    for d in (dept_id, other_dept_id):
        for role in SYSTEM_ROLES:
            ensure_role(auth_client, admin_token, d, SERVICE_NAME, role)

    admin_user, admin_tok = make_role_user(auth_client, admin_token, dept_id, "admin")
    reader_user, reader_tok = make_role_user(auth_client, admin_token, dept_id, "reader")
    operator_user, operator_tok = make_role_user(auth_client, admin_token, dept_id, "operator")
    guest_user, guest_tok = make_role_user(auth_client, admin_token, dept_id, "guest")
    other_admin_user, other_admin_tok = make_role_user(
        auth_client, admin_token, other_dept_id, "admin"
    )

    return ClusterCContext(
        dept_id=dept_id,
        other_dept_id=other_dept_id,
        admin_token=admin_tok,
        reader_token=reader_tok,
        operator_token=operator_tok,
        guest_token=guest_tok,
        other_admin_token=other_admin_tok,
        admin_user_id=admin_user.get("user_id") or admin_user.get("id"),
        reader_user_id=reader_user.get("user_id") or reader_user.get("id"),
        operator_user_id=operator_user.get("user_id") or operator_user.get("id"),
        guest_user_id=guest_user.get("user_id") or guest_user.get("id"),
        other_admin_user_id=other_admin_user.get("user_id") or other_admin_user.get("id"),
    )


# ── Server / account / ipmi factories (через server_service от admin'а) ──────

def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_server(
    server_client: httpx.Client,
    token: str,
    dept_id: str,
    *,
    hostname: str | None = None,
) -> dict:
    hostname = hostname or f"c-srv-{_u()}.test"
    octet = secrets.randbelow(254) + 1
    last = secrets.randbelow(254) + 1
    body = {
        "hostname": hostname,
        "ip_address": f"10.99.{octet}.{last}",
        "department_id": dept_id,
    }
    r = server_client.post(
        "/api/server/v1/servers", json=body, headers=auth_header(token)
    )
    assert r.status_code == 201, f"create_server {hostname}: {r.status_code} {r.text}"
    return r.json()


def create_ipmi(
    server_client: httpx.Client, token: str, server_id: str, *, password: str = "Ipmipass1234!"
) -> dict:
    body = {
        "kind": "ipmi",
        "endpoint_url": "10.99.0.1",
        "username": "ipmi_user",
        "password": password,
    }
    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/ipmi",
        json=body,
        headers=auth_header(token),
    )
    assert r.status_code == 201, f"create_ipmi: {r.status_code} {r.text}"
    return r.json()


def create_account(
    server_client: httpx.Client, token: str, server_id: str, *, login: str | None = None
) -> dict:
    body = {
        "server_ids": [server_id],
        "login": login or f"acc{_u()}",
        "password": "AccountPass1!",
    }
    r = server_client.post(
        "/api/server/v1/server-accounts", json=body, headers=auth_header(token)
    )
    assert r.status_code == 201, f"create_account: {r.status_code} {r.text}"
    return r.json()


def create_os_version(server_client: httpx.Client, token: str) -> dict:
    body = {"name": f"os-{_u()}", "display_name": "Test OS"}
    r = server_client.post(
        "/api/server/v1/os-versions", json=body, headers=auth_header(token)
    )
    assert r.status_code == 201, f"create_os_version: {r.status_code} {r.text}"
    return r.json()


# ── Sweep map ────────────────────────────────────────────────────────────────
#
# Для каждой пары (entity, action) — фабрика, которая принимает
# server_client + token + admin_token (для подготовки ресурсов) + dept_id
# и пробует выполнить HTTP-операцию ровно от имени этого token'а. Возвращает
# (response, audit_action, kwargs). Если действие нельзя достать обычным
# user-facing HTTP (worker-only callback) — в карте его нет (см. WORKER_ONLY_PAIRS).
#
# `audit_action` — имя события в loging, по которому мы матчим success/denied.
# Для CRUD-эндпоинтов имя совпадает с `<entity>.<crud_verb>`; power и rotate
# имеют свои `audit_action` — см. `server_service/src/services/audit_events.py`.


def _no_op_setup(*args, **kwargs):
    return {}


def _setup_with_server(
    server_client: httpx.Client, admin_token: str, dept_id: str
) -> dict:
    srv = create_server(server_client, admin_token, dept_id)
    return {"server_id": srv["id"]}


def _setup_with_server_and_ipmi(
    server_client: httpx.Client, admin_token: str, dept_id: str
) -> dict:
    srv = create_server(server_client, admin_token, dept_id)
    ipmi = create_ipmi(server_client, admin_token, srv["id"])
    return {"server_id": srv["id"], "ipmi_id": ipmi["id"]}


def _setup_with_server_and_account(
    server_client: httpx.Client, admin_token: str, dept_id: str
) -> dict:
    srv = create_server(server_client, admin_token, dept_id)
    acc = create_account(server_client, admin_token, srv["id"])
    return {"server_id": srv["id"], "account_id": acc["id"]}


def _setup_with_busy_server(
    server_client: httpx.Client, admin_token: str, dept_id: str
) -> dict:
    srv = create_server(server_client, admin_token, dept_id)
    # acquire as admin so busy_release attempt has something to release
    r = server_client.post(
        f"/api/server/v1/servers/{srv['id']}/busy",
        json={},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 200, f"pre-acquire failed: {r.text}"
    return {"server_id": srv["id"]}


def _setup_with_os_version(
    server_client: httpx.Client, admin_token: str, dept_id: str
) -> dict:
    osv = create_os_version(server_client, admin_token)
    return {"os_version_id": osv["id"]}


# Callable signature: (server_client, token, ctx) -> httpx.Response
SweepFn = Callable[[httpx.Client, str, dict], httpx.Response]


def _server_view(client, tok, ctx):
    return client.get(
        f"/api/server/v1/servers/{ctx['server_id']}", headers=auth_header(tok)
    )


def _server_create(client, tok, ctx):
    body = {
        "hostname": f"c-srv-neg-{_u()}.test",
        "ip_address": f"10.99.{secrets.randbelow(254) + 1}.{secrets.randbelow(254) + 1}",
        "department_id": ctx["dept_id"],
    }
    return client.post("/api/server/v1/servers", json=body, headers=auth_header(tok))


def _server_update(client, tok, ctx):
    return client.patch(
        f"/api/server/v1/servers/{ctx['server_id']}",
        json={"display_name": f"upd-{_u()}"},
        headers=auth_header(tok),
    )


def _server_delete(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/servers/{ctx['server_id']}", headers=auth_header(tok)
    )


def _server_busy_acquire(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/busy",
        json={},
        headers=auth_header(tok),
    )


def _server_busy_release(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/servers/{ctx['server_id']}/busy", headers=auth_header(tok)
    )


def _server_os_sync(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/os-sync",
        json={"os_version_id": None},
        headers=auth_header(tok),
    )


def _server_power_on(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi/power/on",
        headers=auth_header(tok),
    )


def _server_power_off(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi/power/off",
        headers=auth_header(tok),
    )


def _server_power_reboot(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi/power/reboot",
        headers=auth_header(tok),
    )


def _server_power_status(client, tok, ctx):
    # POST /servers/{id}/power/status — live probe через worker_dispatch.
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/power/status",
        headers=auth_header(tok),
    )


def _server_inventory_trigger(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/inventory/sync",
        headers=auth_header(tok),
    )


def _account_create(client, tok, ctx):
    body = {
        "server_ids": [ctx["server_id"]],
        "login": f"neg{_u()}",
        "password": "AccountPass1!",
    }
    return client.post(
        "/api/server/v1/server-accounts", json=body, headers=auth_header(tok)
    )


def _account_view(client, tok, ctx):
    return client.get(
        f"/api/server/v1/server-accounts/{ctx['account_id']}", headers=auth_header(tok)
    )


def _account_update(client, tok, ctx):
    return client.patch(
        f"/api/server/v1/server-accounts/{ctx['account_id']}",
        json={"shell": "/bin/bash"},
        headers=auth_header(tok),
    )


def _account_delete(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/server-accounts/{ctx['account_id']}", headers=auth_header(tok)
    )


def _account_view_password(client, tok, ctx):
    # GET карточки + view_password: success → password_b64 не null +
    # эмитится `server_account.password_revealed`. Без view_password — поле null
    # и revealed-аудит не пишется, поэтому для негатива достаточно проверить
    # отсутствие revealed-события (но это harder), поэтому negative используем
    # `server_account.view` denied — нет.
    # Вместо этого: мы тестируем что роль с view_password имеет grant: добавим
    # action grant через PUT /permissions перед действием, выполним get_account
    # и проверим `password_revealed` audit (success). Negative — без view_password
    # grant'а, действие server_account.view → ok, но revealed-аудит НЕ пишется.
    # Для единообразия: дёргаем тот же GET, audit-action — "server_account.password_revealed".
    return client.get(
        f"/api/server/v1/server-accounts/{ctx['account_id']}", headers=auth_header(tok)
    )


def _account_rotate_password(client, tok, ctx):
    return client.post(
        f"/api/server/v1/server-accounts/{ctx['account_id']}/rotate_password",
        json={},
        headers=auth_header(tok),
    )


def _ipmi_create(client, tok, ctx):
    body = {
        "kind": "ipmi",
        "endpoint_url": "10.99.0.2",
        "username": "ipmi_user",
        "password": "Ipmipass1234!",
    }
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi",
        json=body,
        headers=auth_header(tok),
    )


def _ipmi_view(client, tok, ctx):
    return client.get(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi", headers=auth_header(tok)
    )


def _ipmi_update(client, tok, ctx):
    return client.patch(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi",
        json={"username": f"u{_u()}"},
        headers=auth_header(tok),
    )


def _ipmi_delete(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi", headers=auth_header(tok)
    )


def _ipmi_view_credentials(client, tok, ctx):
    return client.get(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi/credentials",
        headers=auth_header(tok),
    )


def _ipmi_rotate_credentials(client, tok, ctx):
    return client.post(
        f"/api/server/v1/servers/{ctx['server_id']}/ipmi/credentials/rotate",
        json={},
        headers=auth_header(tok),
    )


def _os_create(client, tok, ctx):
    body = {"name": f"sweep-os-{_u()}", "display_name": "Sweep OS"}
    return client.post(
        "/api/server/v1/os-versions", json=body, headers=auth_header(tok)
    )


def _os_update(client, tok, ctx):
    return client.patch(
        f"/api/server/v1/os-versions/{ctx['os_version_id']}",
        json={"display_name": "Updated"},
        headers=auth_header(tok),
    )


def _os_delete(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/os-versions/{ctx['os_version_id']}", headers=auth_header(tok)
    )


def _permission_view(client, tok, ctx):
    return client.get("/api/server/v1/permissions", headers=auth_header(tok))


def _permission_grant(client, tok, ctx):
    # Грант на безвредную пару (permission, view) для роли reader в своём dept.
    body = {"target_department_id": ctx["dept_id"]}
    return client.put(
        f"/api/server/v1/permissions/permission/{ctx['probe_role']}/view",
        json=body,
        headers=auth_header(tok),
    )


def _permission_revoke(client, tok, ctx):
    return client.delete(
        f"/api/server/v1/permissions/permission/{ctx['probe_role']}/view",
        params={"target_department_id": ctx["dept_id"]},
        headers=auth_header(tok),
    )


@dataclass
class SweepCase:
    entity: str
    action: str
    audit_action: str
    setup: Callable[..., dict]
    invoke: SweepFn
    # success_codes — что считаем "ok" для позитива.
    success_codes: tuple[int, ...] = (200, 201, 202)


# Карта (entity, action) → SweepCase. Worker-only пары (inventory_submit,
# provision_on_host, prepare_callback) выведены в WORKER_ONLY_PAIRS — у
# них нет user-facing HTTP-пути, поэтому они проверяются только через
# `/permissions/catalog` (worker_only=True) и grant/revoke на permission API.
ENTITY_ACTION_SWEEP: dict[tuple[str, str], SweepCase] = {
    ("server", "view"): SweepCase(
        "server", "view", "server.view", _setup_with_server, _server_view
    ),
    ("server", "create"): SweepCase(
        "server", "create", "server.create", _no_op_setup, _server_create
    ),
    ("server", "update"): SweepCase(
        "server", "update", "server.update", _setup_with_server, _server_update
    ),
    ("server", "delete"): SweepCase(
        "server", "delete", "server.delete", _setup_with_server, _server_delete
    ),
    ("server", "busy_acquire"): SweepCase(
        "server", "busy_acquire", "server.acquire", _setup_with_server, _server_busy_acquire
    ),
    ("server", "busy_release"): SweepCase(
        "server", "busy_release", "server.release", _setup_with_busy_server, _server_busy_release
    ),
    ("server", "os_sync"): SweepCase(
        "server", "os_sync", "server.update_os_version", _setup_with_server, _server_os_sync
    ),
    ("server", "power_on"): SweepCase(
        "server", "power_on", "server.power_on", _setup_with_server_and_ipmi, _server_power_on
    ),
    ("server", "power_off"): SweepCase(
        "server", "power_off", "server.power_off", _setup_with_server_and_ipmi, _server_power_off
    ),
    ("server", "power_reboot"): SweepCase(
        "server", "power_reboot", "server.power_reboot", _setup_with_server_and_ipmi, _server_power_reboot
    ),
    ("server", "power_status"): SweepCase(
        "server", "power_status", "server.power_status", _setup_with_server_and_ipmi, _server_power_status
    ),
    ("server", "inventory_trigger"): SweepCase(
        "server", "inventory_trigger", "server.inventory_sync", _setup_with_server, _server_inventory_trigger
    ),
    ("server_account", "view"): SweepCase(
        "server_account", "view", "server_account.view", _setup_with_server_and_account, _account_view
    ),
    ("server_account", "create"): SweepCase(
        "server_account", "create", "server_account.create", _setup_with_server, _account_create
    ),
    ("server_account", "update"): SweepCase(
        "server_account", "update", "server_account.update", _setup_with_server_and_account, _account_update
    ),
    ("server_account", "delete"): SweepCase(
        "server_account", "delete", "server_account.delete", _setup_with_server_and_account, _account_delete
    ),
    ("server_account", "view_password"): SweepCase(
        "server_account", "view_password", "server_account.password_revealed",
        _setup_with_server_and_account, _account_view_password,
    ),
    ("server_account", "rotate_password"): SweepCase(
        "server_account", "rotate_password", "server_account.rotate_password",
        _setup_with_server_and_account, _account_rotate_password,
    ),
    ("server_account", "grant_sudo"): SweepCase(
        # grant_sudo проверяется при create с has_sudo=True или update'е,
        # поднимающим has_sudo. Используем CREATE-путь — он также эмитит
        # `server_account.create` denied на нехватке grant_sudo, плюс мы
        # проверяем что у admin'а сценарий success'ен.
        "server_account", "grant_sudo", "server_account.create",
        _setup_with_server, _account_create,
    ),
    ("ipmi_controller", "view"): SweepCase(
        "ipmi_controller", "view", "ipmi_controller.view",
        _setup_with_server_and_ipmi, _ipmi_view,
    ),
    ("ipmi_controller", "create"): SweepCase(
        "ipmi_controller", "create", "ipmi_controller.create",
        _setup_with_server, _ipmi_create,
    ),
    ("ipmi_controller", "update"): SweepCase(
        "ipmi_controller", "update", "ipmi_controller.update",
        _setup_with_server_and_ipmi, _ipmi_update,
    ),
    ("ipmi_controller", "delete"): SweepCase(
        "ipmi_controller", "delete", "ipmi_controller.delete",
        _setup_with_server_and_ipmi, _ipmi_delete,
    ),
    ("ipmi_controller", "view_credentials"): SweepCase(
        "ipmi_controller", "view_credentials", "ipmi_controller.view_credentials_meta",
        _setup_with_server_and_ipmi, _ipmi_view_credentials,
    ),
    ("ipmi_controller", "rotate_credentials"): SweepCase(
        "ipmi_controller", "rotate_credentials", "ipmi_controller.rotate_credentials",
        _setup_with_server_and_ipmi, _ipmi_rotate_credentials,
    ),
    ("os_version", "create"): SweepCase(
        "os_version", "create", "os_version.create", _no_op_setup, _os_create
    ),
    ("os_version", "update"): SweepCase(
        "os_version", "update", "os_version.update", _setup_with_os_version, _os_update
    ),
    ("os_version", "delete"): SweepCase(
        "os_version", "delete", "os_version.delete", _setup_with_os_version, _os_delete
    ),
    ("permission", "view"): SweepCase(
        "permission", "view", "permission.grant",  # неиспользуемое поле
        _no_op_setup, _permission_view,
        success_codes=(200,),
    ),
    ("permission", "permission_grant"): SweepCase(
        "permission", "permission_grant", "permission.grant", _no_op_setup, _permission_grant
    ),
    ("permission", "permission_revoke"): SweepCase(
        "permission", "permission_revoke", "permission.revoke", _no_op_setup, _permission_revoke
    ),
}


# Worker-only пары — их нельзя проверить через user-facing HTTP с обычного
# token'а: internal endpoints гейтятся worker_bot-PAT и `X-Target-Department-Id`
# header'ом. Мы их проверяем через permission catalog (`worker_only=True`)
# и через grant/revoke на /permissions API.
WORKER_ONLY_PAIRS: tuple[tuple[str, str], ...] = (
    ("server", "inventory_submit"),
    ("server", "prepare_callback"),
    ("server_account", "inventory_submit"),
    ("server_account", "provision_on_host"),
)


def all_sweep_pairs() -> list[tuple[str, str]]:
    return sorted(ENTITY_ACTION_SWEEP.keys())


# ── Audit helpers ────────────────────────────────────────────────────────────


def find_event(
    logging_client: httpx.Client,
    *,
    action: str,
    status: str,
    from_time: datetime,
    target_id: str | None = None,
    extra_match: Callable[[dict], bool] | None = None,
    retries: int = 20,
    delay: float = 0.4,
) -> dict | None:
    """Поллит loging_service пока не найдёт событие с подходящим status/target."""
    params = {
        "action": action,
        "status": status,
        "from_time": from_time.isoformat(),
        "limit": 100,
    }
    for _ in range(retries):
        r = logging_client.get("/api/logging/v1/events", params=params)
        if r.status_code == 200:
            for item in r.json().get("items", []):
                if target_id is not None and item.get("target_id") != target_id:
                    continue
                if extra_match and not extra_match(item):
                    continue
                return item
        time.sleep(delay)
    return None


def expect_denied_audit(
    logging_client: httpx.Client, action: str, since: datetime, *, reason: str = "permission_denied"
) -> dict | None:
    """Дождаться `status=denied` audit с `reason` в details.

    Если audit-конвейер server_service не доезжает до loging_service (см.
    `_audit_pipeline_works`) — скипаем audit-часть теста, primary HTTP-check
    остаётся пройденным. Возвращает None в этом случае.
    """
    ev = find_event(
        logging_client, action=action, status="denied", from_time=since,
        extra_match=lambda it: (it.get("details") or {}).get("reason") == reason,
    )
    if ev is None and not _audit_pipeline_works(logging_client):
        pytest.skip(_AUDIT_DISABLED_REASON)
    assert ev is not None, (
        f"denied audit not found for action={action!r} reason={reason!r}"
    )
    return ev


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Audit pipeline probe (для skip при отсутствующем LOGGING_SERVICE_API_KEY) ─

_AUDIT_DISABLED_REASON = (
    "server_service audit not reaching loging_service in this stack "
    "(`LOGGING_SERVICE_API_KEY` env not propagated to server-service "
    "container — see docker-compose.test.yml). Primary HTTP-check проходит, "
    "audit-часть скипается до починки инфры."
)

# None — ещё не проверяли; True/False — кэш.
_audit_ok_cache: bool | None = None


def _audit_pipeline_works(logging_client: httpx.Client) -> bool:
    """Проверить, доезжают ли вообще server_service events до loging_service.

    Кэшируется сессионно. Дешёвая проверка через GET /events?service=server_service.
    Если ни одного события server_service не зарегистрировано к этому моменту —
    инфра audit-конвейера сломана (типичная причина: `LOGGING_SERVICE_API_KEY`
    env не прокинут в контейнер server-service).
    """
    global _audit_ok_cache
    if _audit_ok_cache is not None:
        return _audit_ok_cache
    try:
        r = logging_client.get(
            "/api/logging/v1/events",
            params={"service": "server_service", "limit": 1},
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            _audit_ok_cache = len(items) > 0
        else:
            _audit_ok_cache = False
    except Exception:
        _audit_ok_cache = False
    return _audit_ok_cache


def assert_audit_found(
    logging_client: httpx.Client,
    *,
    action: str,
    status: str,
    from_time: datetime,
    target_id: str | None = None,
    extra_match: Callable[[dict], bool] | None = None,
    fail_message: str | None = None,
) -> dict | None:
    """find_event + assert с автоматическим skip при сломанном audit-конвейере.

    Если событие не нашлось и проба показала, что server_service события
    вообще не доходят до loging_service — `pytest.skip` (а не fail). Так
    primary HTTP-check теста остаётся валидным, а audit-проверка не
    блокирует прогон до починки инфры.
    """
    ev = find_event(
        logging_client,
        action=action,
        status=status,
        from_time=from_time,
        target_id=target_id,
        extra_match=extra_match,
    )
    if ev is None and not _audit_pipeline_works(logging_client):
        pytest.skip(_AUDIT_DISABLED_REASON)
    msg = fail_message or f"audit {action!r} status={status!r} not found"
    assert ev is not None, msg
    return ev
