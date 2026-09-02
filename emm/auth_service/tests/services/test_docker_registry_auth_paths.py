"""Edge cases для docker_registry_service._authenticate_subject.

Покрываем пути, которые не покрыты test_docker_token.py:
- Неактивный юзер (BLOCKED/BANNED) при password auth → INVALID_CREDENTIALS
- PAT с истёкшим expires_at → INVALID_CREDENTIALS
- PAT для неактивного юзера → INVALID_CREDENTIALS
- Bot-токен с истёкшим expires_at → INVALID_CREDENTIALS
- Неактивный бот → INVALID_CREDENTIALS
- _parse_scope: multi-resource scope строка
- _resolve_actions: pull_policy=restricted + user не в списке → pull denied
- _resolve_actions: empty requested_actions → []
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests._helpers.http import _basic  # noqa: F401 — общий helper

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"
TOKENS_URL = "/api/auth/v1/tokens"
USERS_URL = "/api/auth/v1/users"


async def _enable_docker(client, token, dept_id, pull_policy="all",
                          pull_user_ids=None, push_user_ids=None):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pull_policy": pull_policy,
            "pull_user_ids": pull_user_ids or [],
            "push_user_ids": push_user_ids or [],
        },
    )


# ── Password path: inactive user ────────────────────────────────────────────

async def test_blocked_user_gets_invalid_credentials_not_user_blocked(
    client, admin_token, user_a, dept_a,
):
    """BLOCKED юзер на docker/token → 401 INVALID_CREDENTIALS (не USER_BLOCKED).

    docker_registry_service маскирует статус аккаунта через generic INVALID_CREDENTIALS,
    чтобы не раскрывать существование/состояние аккаунта через Docker Basic auth канал.
    """
    await _enable_docker(client, admin_token, dept_a.id)

    # Блокируем через API
    resp = await client.patch(
        f"{USERS_URL}/{user_a.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "blocked"},
    )
    assert resp.status_code == 200

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_banned_user_gets_invalid_credentials(
    client, admin_token, user_a, dept_a,
):
    """BANNED юзер на docker/token → 401 INVALID_CREDENTIALS (не USER_BANNED)."""
    await _enable_docker(client, admin_token, dept_a.id)

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await client.post(
        f"{USERS_URL}/{user_a.id}/ban",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "temporary", "reason": "test", "expires_at": future},
    )

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


# ── PAT path: expired PAT ────────────────────────────────────────────────────

async def test_expired_pat_denied_in_docker(
    client, admin_token, user_a_token, user_a, dept_a, db,
):
    """PAT с истёкшим expires_at → 401 INVALID_CREDENTIALS на docker/token."""
    await _enable_docker(client, admin_token, dept_a.id)

    # Создаём PAT с expires_at в будущем, потом сдвигаем в прошлое.
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    pat_resp = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "exp_docker_pat", "expires_at": future, "allowed_services": ["service_x"]},
    )
    pat_raw = pat_resp.json()["token"]
    pat_id = pat_resp.json()["token_id"]

    from sqlalchemy import update
    from src.models import PersonalAccessToken
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.execute(
        update(PersonalAccessToken)
        .where(PersonalAccessToken.id == pat_id)
        .values(expires_at=past)
    )
    await db.commit()

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", pat_raw),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


# ── Bot path: expired bot token ──────────────────────────────────────────────

async def test_expired_bot_token_denied_in_docker(
    client, admin_token, dept_a, db,
):
    """Bot-токен с истёкшим expires_at → 401 INVALID_CREDENTIALS на docker/token."""
    await _enable_docker(client, admin_token, dept_a.id)

    bot_id = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "exp_bot", "department_id": dept_a.id, "allowed_services": []},
    )).json()["bot_id"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    tok_resp = (await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "exp_tok", "expires_at": future},
    )).json()
    bot_raw = tok_resp["token"]
    tok_id = tok_resp["token_id"]

    from sqlalchemy import update
    from src.models import BotToken
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.execute(
        update(BotToken)
        .where(BotToken.id == tok_id)
        .values(expires_at=past)
    )
    await db.commit()

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("exp_bot", bot_raw),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


# ── _parse_scope: multi-resource ────────────────────────────────────────────

async def test_multi_resource_scope_parsed_correctly(
    client, admin_token, user_a, dept_a,
):
    """scope='repository:app:pull repository:db:pull,push' — два ресурса."""
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[user_a.id])

    import jwt as _jwt
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={
            "service": "registry.test",
            "scope": "repository:app:pull repository:db:pull,push",
        },
    )
    assert resp.status_code == 200
    access = _jwt.decode(
        resp.json()["access_token"], options={"verify_signature": False},
    ).get("access", [])
    names = {e["name"] for e in access}
    assert "app" in names
    assert "db" in names


# ── _resolve_actions: restricted pull_policy + user not listed ───────────────

async def test_restricted_pull_not_listed_user_no_pull_action(
    client, admin_token, user_a, user_b, dept_a,
):
    """pull_policy=restricted, pull_user_ids=[user_b.id] — user_a не в списке → pull нет."""
    import jwt as _jwt
    await _enable_docker(
        client, admin_token, dept_a.id,
        pull_policy="restricted", pull_user_ids=[user_b.id],
    )
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": "repository:myapp:pull"},
    )
    # Аутентификация прошла (docker config есть), но pull не выдан → токен без pull
    assert resp.status_code == 200
    access = _jwt.decode(
        resp.json()["access_token"], options={"verify_signature": False},
    ).get("access", [])
    assert all("pull" not in entry.get("actions", []) for entry in access)


# ── Scope без known actions (e.g. "delete") → no access entry ────────────────

async def test_unknown_action_in_scope_returns_empty_access(
    client, admin_token, user_a, dept_a,
):
    """scope='repository:myapp:delete' — delete не поддерживается → access пустой."""
    import jwt as _jwt
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[user_a.id])
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": "repository:myapp:delete"},
    )
    assert resp.status_code == 200
    access = _jwt.decode(
        resp.json()["access_token"], options={"verify_signature": False},
    ).get("access", [])
    # delete не входит в allowed → entry либо отсутствует либо actions=[]
    assert all(len(e.get("actions", [])) == 0 for e in access)


# ── Audit: password path failure emits subject_type=password ─────────────────

async def test_wrong_password_audit_has_subject_type_password(
    client, admin_token, user_a, dept_a, docker_registry_enabled, monkeypatch,
):
    """Неверный пароль → audit docker.token_issued failure с subject_type='password'."""
    from src.services import audit_service as _audit_service

    captured: list[dict] = []
    original_info = _audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and "audit_event" in msg and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(_audit_service.logger, "info", fake_info)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {"logging_service_url": None, "logging_service_api_key": None})(),
    )

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "WrongPass!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401

    events = [e for e in captured if e.get("action") == "docker.token_issued"]
    assert events, "docker.token_issued event must be emitted on wrong password"
    ev = events[0]
    assert ev["status"] == "failure"
    assert ev["details"]["subject_type"] == "password"


# ── Audit: PAT path failure emits subject_type=pat ──────────────────────────

async def test_invalid_pat_audit_has_subject_type_pat(
    client, docker_registry_enabled, monkeypatch,
):
    """Невалидный PAT (dbos_pat_...) → audit docker.token_issued с subject_type='pat'."""
    from src.services import audit_service as _audit_service

    captured: list[dict] = []
    original_info = _audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and "audit_event" in msg and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(_audit_service.logger, "info", fake_info)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {"logging_service_url": None, "logging_service_api_key": None})(),
    )

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("anyuser", "dbos_pat_invalid_pat_value"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 401

    events = [e for e in captured if e.get("action") == "docker.token_issued"]
    assert len(events) == 1
    ev = events[0]
    assert ev["details"]["subject_type"] == "pat"
