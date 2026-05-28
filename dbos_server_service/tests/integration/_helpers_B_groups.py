"""Хелперы для E2E groups + bots-in-groups сценариев (cluster B).

Используем только conftest-фикстуры. Утилиты:
    * уникальные суффиксы и идемпотентное создание dept/service/role/grant;
    * фабрики dept-scoped юзеров (account_admin / department_admin / regular);
    * прямой SELECT по auth_db_test / loging_db_test;
    * polling-обёртка над `audit_events`.
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text


AUTH_PREFIX = "/api/auth/v1"
SERVICE_API_KEY = os.environ.get("LOGGING_SERVICE_API_KEY", "test-logging-api-key")


def rand_suffix(n: int = 8) -> str:
    return secrets.token_hex(max(2, n // 2))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Auth-API: idempotent setup ───────────────────────────────────────────────

def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    r = auth_client.post(
        f"{AUTH_PREFIX}/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    listing = auth_client.get(
        f"{AUTH_PREFIX}/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    for d in listing:
        if d["name"] == name:
            return d["department_id"]
    raise AssertionError(f"failed to create or find department {name!r}: {r.status_code} {r.text}")


def ensure_service(auth_client: httpx.Client, admin_token: str, name: str) -> None:
    auth_client.post(
        f"{AUTH_PREFIX}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": name, "display_name": name.title()},
    )
    # 201/409 — обе ок (catalog бывает уже есть)


def grant_service_to_dept(
    auth_client: httpx.Client, admin_token: str, dept_id: str, service: str
) -> None:
    auth_client.post(
        f"{AUTH_PREFIX}/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service},
    )


def ensure_role(
    auth_client: httpx.Client, admin_token: str, dept_id: str, service: str,
    role_name: str, display_name: str | None = None,
) -> None:
    r = auth_client.post(
        f"{AUTH_PREFIX}/departments/{dept_id}/services/{service}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "role_name": role_name,
            "display_name": display_name or role_name.title(),
        },
    )
    if r.status_code not in (200, 201, 409):
        raise RuntimeError(f"create role {role_name}: {r.status_code} {r.text}")


# ── User / token helpers ─────────────────────────────────────────────────────

def login(auth_client: httpx.Client, username: str, password: str) -> str:
    r = auth_client.post(
        f"{AUTH_PREFIX}/login",
        json={"username": username, "password": password},
    )
    if r.status_code != 200:
        raise AssertionError(f"login({username}) failed: {r.status_code} {r.text}")
    return r.json()["access_token"]


def make_dept_user(
    auth_client: httpx.Client, admin_token: str, dept_id: str,
    *, platform_role: str | None = None, username: str | None = None,
    password: str = "UserPass1!",
) -> dict:
    """Создать юзера в указанном dept'е с опциональной platform_role.
    Возвращает body /users (содержит `user_id`)."""
    body = {
        "username": username or f"u_{rand_suffix()}",
        "password": password,
        "department_id": dept_id,
    }
    if platform_role is not None:
        body["platform_role"] = platform_role
    r = auth_client.post(
        f"{AUTH_PREFIX}/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    if r.status_code not in (200, 201):
        raise AssertionError(f"make_dept_user failed: {r.status_code} {r.text}")
    data = r.json()
    data.setdefault("username", body["username"])
    data["_password"] = password
    return data


def make_bot_in_dept(
    auth_client: httpx.Client, admin_token: str, dept_id: str,
    *, name: str | None = None, allowed_services: list[str] | None = None,
) -> dict:
    body = {
        "name": name or f"bot_{rand_suffix()}",
        "department_id": dept_id,
    }
    if allowed_services is not None:
        body["allowed_services"] = allowed_services
    r = auth_client.post(
        f"{AUTH_PREFIX}/bots",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    if r.status_code not in (200, 201):
        raise AssertionError(f"make_bot_in_dept failed: {r.status_code} {r.text}")
    return r.json()


def issue_bot_token(auth_client: httpx.Client, admin_token: str, bot_id: str) -> dict:
    r = auth_client.post(
        f"{AUTH_PREFIX}/bots/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": f"tok_{rand_suffix()}"},
    )
    if r.status_code not in (200, 201):
        raise AssertionError(f"issue_bot_token failed: {r.status_code} {r.text}")
    return r.json()


# ── Group operations ─────────────────────────────────────────────────────────

def create_group(
    auth_client: httpx.Client, token: str, dept_id: str,
    *, name: str | None = None, display_name: str | None = None,
) -> httpx.Response:
    return auth_client.post(
        f"{AUTH_PREFIX}/groups",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "department_id": dept_id,
            "name": name or f"g_{rand_suffix()}",
            "display_name": display_name or "G",
        },
    )


def patch_group(auth_client, token, group_id, **fields) -> httpx.Response:
    return auth_client.patch(
        f"{AUTH_PREFIX}/groups/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=fields,
    )


def delete_group(auth_client, token, group_id) -> httpx.Response:
    return auth_client.delete(
        f"{AUTH_PREFIX}/groups/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def add_member(auth_client, token, group_id, user_id) -> httpx.Response:
    return auth_client.post(
        f"{AUTH_PREFIX}/groups/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": user_id},
    )


def remove_member(auth_client, token, group_id, user_id) -> httpx.Response:
    return auth_client.delete(
        f"{AUTH_PREFIX}/groups/{group_id}/members/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def add_bot_member(auth_client, token, group_id, bot_id) -> httpx.Response:
    return auth_client.post(
        f"{AUTH_PREFIX}/groups/{group_id}/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"bot_id": bot_id},
    )


def remove_bot_member(auth_client, token, group_id, bot_id) -> httpx.Response:
    return auth_client.delete(
        f"{AUTH_PREFIX}/groups/{group_id}/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def grant_service_to_group(auth_client, token, group_id, service_name) -> httpx.Response:
    return auth_client.post(
        f"{AUTH_PREFIX}/groups/{group_id}/services",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name},
    )


def revoke_service_from_group(auth_client, token, group_id, service_name) -> httpx.Response:
    return auth_client.delete(
        f"{AUTH_PREFIX}/groups/{group_id}/services/{service_name}",
        headers={"Authorization": f"Bearer {token}"},
    )


def assign_group_roles(auth_client, token, group_id, service_name, roles) -> httpx.Response:
    return auth_client.post(
        f"{AUTH_PREFIX}/groups/{group_id}/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name, "roles": roles},
    )


# ── Introspect (service-to-service) ──────────────────────────────────────────

def introspect(
    auth_client: httpx.Client, subject_token: str, *,
    service_identity: str = "server_service",
) -> dict:
    """Service-to-service POST /authorization/introspect. Возвращает body."""
    r = auth_client.post(
        f"{AUTH_PREFIX}/authorization/introspect",
        headers={
            "Authorization": f"Bearer {SERVICE_API_KEY}",
            "X-Service-Identity": service_identity,
        },
        json={"token": subject_token},
    )
    if r.status_code != 200:
        raise AssertionError(f"introspect failed: {r.status_code} {r.text}")
    return r.json()


# ── DB-level assertions ──────────────────────────────────────────────────────

def query_audit_events(
    loging_db_engine, *, action: str,
    status: str | None = None,
    actor_id: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    where = ["action = :action"]
    params: dict = {"action": action, "limit": limit}
    if status is not None:
        where.append("status = :status")
        params["status"] = status
    if actor_id is not None:
        where.append("actor_id = :actor_id")
        params["actor_id"] = actor_id
    if target_id is not None:
        where.append("target_id = :target_id")
        params["target_id"] = target_id
    if since is not None:
        where.append("timestamp >= :since")
        params["since"] = since
    sql = (
        "SELECT id, service, action, actor_id, actor_type, target_id, target_type, "
        "status, allowed, severity, details, department_id "
        f"FROM audit_events WHERE {' AND '.join(where)} "
        "ORDER BY timestamp DESC LIMIT :limit"
    )
    with loging_db_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def wait_for_audit_row(
    loging_db_engine, *, action: str,
    status: str | None = None,
    actor_id: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    retries: int = 25, delay: float = 0.4,
) -> dict:
    for _ in range(retries):
        rows = query_audit_events(
            loging_db_engine, action=action, status=status,
            actor_id=actor_id, target_id=target_id, since=since, limit=5,
        )
        if rows:
            return rows[0]
        time.sleep(delay)
    raise AssertionError(
        f"audit row not found: action={action!r} status={status!r} "
        f"actor_id={actor_id!r} target_id={target_id!r}"
    )


def group_db_row(auth_db_engine, group_id: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, department_id, name, display_name, description, "
                "is_active, created_at, created_by FROM user_groups WHERE id = :id"
            ),
            {"id": group_id},
        ).mappings().first()
    return dict(row) if row else None


def member_db_row(auth_db_engine, group_id: str, user_id: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, group_id, user_id, added_at, added_by "
                "FROM user_group_memberships WHERE group_id = :gid AND user_id = :uid"
            ),
            {"gid": group_id, "uid": user_id},
        ).mappings().first()
    return dict(row) if row else None


def bot_member_db_row(auth_db_engine, group_id: str, bot_id: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, group_id, bot_id, added_at, added_by "
                "FROM bot_group_memberships WHERE group_id = :gid AND bot_id = :bid"
            ),
            {"gid": group_id, "bid": bot_id},
        ).mappings().first()
    return dict(row) if row else None


def group_service_access_row(auth_db_engine, group_id: str, service: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT group_id, service_name, is_active, granted_at, granted_by "
                "FROM group_service_access WHERE group_id = :gid AND service_name = :svc"
            ),
            {"gid": group_id, "svc": service},
        ).mappings().first()
    return dict(row) if row else None


def group_service_roles(auth_db_engine, group_id: str, service: str) -> list[str]:
    with auth_db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT role FROM group_service_roles "
                "WHERE group_id = :gid AND service_name = :svc"
            ),
            {"gid": group_id, "svc": service},
        ).scalars().all()
    return list(rows)
