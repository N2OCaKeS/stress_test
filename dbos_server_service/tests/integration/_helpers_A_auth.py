"""Хелперы для E2E auth-сценариев (cluster A).

Только утилиты: уникальные суффиксы, идемпотентное создание dept/service,
прямые SQL-выборки по auth_db_engine / loging_db_engine. Фикстуры — в
conftest.py, его не трогаем.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text


# ── Generic ──────────────────────────────────────────────────────────────────

def rand_suffix(n: int = 8) -> str:
    return secrets.token_hex(max(2, n // 2))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Auth-API helpers ─────────────────────────────────────────────────────────

def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Создать отдел; если уже есть — найти и вернуть id."""
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    listing = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    for d in listing:
        if d["name"] == name:
            return d["department_id"]
    raise AssertionError(f"failed to create or find department {name!r}: {r.status_code} {r.text}")


def login(auth_client: httpx.Client, username: str, password: str) -> httpx.Response:
    return auth_client.post(
        "/api/auth/v1/login",
        json={"username": username, "password": password},
    )


def me(auth_client: httpx.Client, token: str) -> httpx.Response:
    return auth_client.get(
        "/api/auth/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── DB-level assertions ──────────────────────────────────────────────────────

def query_audit_events(
    loging_db_engine,
    *,
    action: str,
    status: str | None = None,
    actor_id: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Прямой SELECT по loging.audit_events. Возвращает свежие сначала."""
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
    loging_db_engine,
    *,
    action: str,
    status: str | None = None,
    actor_id: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    retries: int = 25,
    delay: float = 0.4,
) -> dict:
    """Polling-обёртка над `query_audit_events` для fire-and-forget audit'а."""
    for _ in range(retries):
        rows = query_audit_events(
            loging_db_engine,
            action=action,
            status=status,
            actor_id=actor_id,
            target_id=target_id,
            since=since,
            limit=5,
        )
        if rows:
            return rows[0]
        time.sleep(delay)
    raise AssertionError(
        f"audit_events row not found: action={action!r} status={status!r} "
        f"actor_id={actor_id!r} target_id={target_id!r}"
    )


def user_db_row(auth_db_engine, user_id: str) -> dict | None:
    """Прочитать строку users по id."""
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, username, status, is_active, platform_role, department_id, "
                "failed_login_attempts, locked_until "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).mappings().first()
    return dict(row) if row else None


def session_count(auth_db_engine, user_id: str, *, active_only: bool = True) -> int:
    """Сколько у юзера сессий (refresh-токенов). active_only фильтрует revoked/expired."""
    sql = "SELECT COUNT(*) FROM sessions WHERE user_id = :uid"
    if active_only:
        sql += " AND revoked_at IS NULL AND expires_at > now()"
    with auth_db_engine.connect() as conn:
        return int(conn.execute(text(sql), {"uid": user_id}).scalar() or 0)


def pat_db_row(auth_db_engine, token_id: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, user_id, name, token_prefix, allowed_services, "
                "created_at, expires_at, revoked_at, revoked_reason "
                "FROM personal_access_tokens WHERE id = :id"
            ),
            {"id": token_id},
        ).mappings().first()
    return dict(row) if row else None


def bot_token_row(auth_db_engine, token_id: str) -> dict | None:
    with auth_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, bot_id, name, revoked_at, expires_at "
                "FROM bot_tokens WHERE id = :id"
            ),
            {"id": token_id},
        ).mappings().first()
    return dict(row) if row else None
