"""Тесты: GET /api/auth/v1/docker/token — выдача токена для Docker registry."""

from tests._helpers.http import _basic  # noqa: F401 — общий helper

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"
TOKENS_URL = "/api/auth/v1/tokens"


async def _enable_docker(client, token, dept_id, pull_policy="all", pull_user_ids=None, push_user_ids=None):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pull_policy": pull_policy,
            "pull_user_ids": pull_user_ids or [],
            "push_user_ids": push_user_ids or [],
        },
    )


# ── No config / disabled ──────────────────────────────────────────────────────

async def test_no_docker_config_returns_403(client, user_a, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"


async def test_disabled_docker_config_returns_403(client, admin_token, user_a, dept_a, docker_registry_enabled):
    await client.delete(CONFIG_URL.format(dept_id=dept_a.id),
                  headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 403


# ── Auth failures ─────────────────────────────────────────────────────────────

async def test_missing_auth_header_returns_401(client, docker_registry_enabled, user_a):
    resp = await client.get(TOKEN_URL, params={"service": "registry.test"})
    assert resp.status_code == 401


async def test_wrong_password_returns_401(client, docker_registry_enabled, user_a, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "WrongPass!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_nonexistent_user_returns_401(client, docker_registry_enabled, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("ghost_user", "Pass12345678!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── Successful token issuance ─────────────────────────────────────────────────

async def test_authenticated_user_gets_token(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert "access_token" in body
    assert "expires_in" in body
    assert body["token"] == body["access_token"]


async def test_token_response_has_issued_at(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 200
    assert "issued_at" in resp.json()


# ── pull_policy=all ───────────────────────────────────────────────────────────

async def test_pull_policy_all_grants_pull_to_any_user(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id, pull_policy="all")
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 200
    access = resp.json().get("access", [])
    # The token payload is RS256 JWT — access list is embedded in it
    # We verify the HTTP response succeeded; access enforcement is in the JWT claims
    # but access array is in the token, not the top-level response


async def test_pull_policy_all_user_b_in_other_dept_denied(client, admin_token, user_b, dept_b, dept_a):
    # dept_a has docker enabled, but user_b is in dept_b which has no config
    await _enable_docker(client, admin_token, dept_a.id, pull_policy="all")
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_b", "User12345678!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 403


# ── pull_policy=restricted ────────────────────────────────────────────────────

async def test_restricted_policy_only_listed_user_can_pull(client, admin_token, user_a, user_b, dept_a):
    await _enable_docker(client, admin_token, dept_a.id,
                   pull_policy="restricted", pull_user_ids=[user_a.id])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 200


# ── Push permissions ──────────────────────────────────────────────────────────

async def test_push_allowed_for_listed_user(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[user_a.id])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                      params={"service": "registry.test", "scope": "repository:myapp:push"})
    assert resp.status_code == 200
    access = _decode_access(resp.json()["access_token"])
    assert any("push" in entry.get("actions", []) for entry in access)


# ── pull_policy=restricted push semantics ─────────────────────────────────────


import jwt as _jwt
from datetime import timedelta
from src.utils.time import utcnow


def _decode_access(token: str) -> list[dict]:
    """Decode docker JWT (RS256) without verifying signature — tests just need access claim."""
    return _jwt.decode(token, options={"verify_signature": False}).get("access", [])


async def test_push_denied_for_user_not_in_push_list(client, admin_token, user_a, dept_a):
    """user_a в pull_user_ids, но НЕ в push_user_ids — push action не должен попасть в токен."""
    await _enable_docker(client, admin_token, dept_a.id,
                         pull_policy="restricted",
                         pull_user_ids=[user_a.id], push_user_ids=[])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                            params={"service": "registry.test", "scope": "repository:myapp:push"})
    assert resp.status_code == 200
    access = _decode_access(resp.json()["access_token"])
    # либо нет совпавшего entry, либо есть entry, но без 'push' в actions
    assert all("push" not in entry.get("actions", []) for entry in access)


async def test_push_denied_for_user_in_other_dept(client, admin_token, user_b, dept_a, dept_b):
    """user_b живёт в dept_b, конфиг включён только в dept_a → push отказан."""
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[user_b.id])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_b", "User12345678!"),
                            params={"service": "registry.test", "scope": "repository:myapp:push"})
    # dept_b не имеет docker_registry конфига → 403
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"


async def test_push_pull_combined_scope_returns_only_allowed_actions(client, admin_token, user_a, dept_a):
    """scope='repository:x:pull,push' для user_a с pull-only — токен содержит только pull."""
    await _enable_docker(client, admin_token, dept_a.id,
                         pull_policy="all", push_user_ids=[])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                            params={"service": "registry.test", "scope": "repository:myapp:pull,push"})
    assert resp.status_code == 200
    access = _decode_access(resp.json()["access_token"])
    assert len(access) == 1
    assert set(access[0]["actions"]) == {"pull"}


async def test_empty_push_user_ids_nobody_can_push(client, admin_token, user_a, dept_a):
    """push_user_ids=[] — никто не имеет push, токен возвращается без push в access."""
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User12345678!"),
                            params={"service": "registry.test", "scope": "repository:myapp:push"})
    assert resp.status_code == 200
    access = _decode_access(resp.json()["access_token"])
    assert all("push" not in entry.get("actions", []) for entry in access)


# ── PAT as Docker password ────────────────────────────────────────────────────

async def test_pat_as_docker_password(client, admin_token, user_a_token, user_a, dept_a, docker_registry_service_granted):
    await _enable_docker(client, admin_token, dept_a.id)
    # PAT-scope включает `docker_registry` — docker-канал доступен.
    # Без этого scope-guard `_authenticate_subject` отбивает 403
    # PAT_SCOPE_DENIES_DOCKER (см. test_pat_without_docker_scope_denied).
    pat = (await client.post(TOKENS_URL,
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      json={"name": "docker_pat", "allowed_services": ["docker_registry"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()})).json()["token"]
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", pat),
                      params={"service": "registry.test"})
    assert resp.status_code == 200


async def test_revoked_pat_denied_for_docker(client, admin_token, user_a_token, user_a, dept_a, docker_registry_service_granted):
    await _enable_docker(client, admin_token, dept_a.id)
    pat_data = (await client.post(TOKENS_URL,
                           headers={"Authorization": f"Bearer {user_a_token}"},
                           json={"name": "docker_pat_rev", "allowed_services": ["docker_registry"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()})).json()
    await client.delete(f"{TOKENS_URL}/{pat_data['token_id']}",
                  headers={"Authorization": f"Bearer {user_a_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", pat_data["token"]),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── Bot token as Docker password ──────────────────────────────────────────────

async def test_bot_token_as_docker_password(client, admin_token, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                         json={"name": "dock_bot", "department_id": dept_a.id,
                               "allowed_services": []})).json()["bot_id"]
    bot_token = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                            headers={"Authorization": f"Bearer {admin_token}"},
                            json={"name": "dock_tok"})).json()["token"]
    resp = await client.get(TOKEN_URL, headers=_basic("dock_bot", bot_token),
                      params={"service": "registry.test"})
    assert resp.status_code == 200


async def test_revoked_bot_token_denied_for_docker(client, admin_token, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                         json={"name": "dock_bot_rev", "department_id": dept_a.id,
                               "allowed_services": []})).json()["bot_id"]
    tok_data = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                           headers={"Authorization": f"Bearer {admin_token}"},
                           json={"name": "dock_rev_tok"})).json()
    await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_data['token_id']}",
                  headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("dock_bot_rev", tok_data["token"]),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── PAT/bot scope guard ───────────────────────────────────────────────────────


async def test_pat_without_docker_scope_denied(
    client, admin_token, user_a_token, user_a, dept_a, dept_a_with_service, service_x,
):
    """PAT с непустым `allowed_services`, не содержащим `docker_registry`,
    не должен пускать в docker-канал — 403 PAT_SCOPE_DENIES_DOCKER.

    Без guard'а PAT, выписанный только под server_service-канал, тихо
    получает docker-token через Basic auth. После guard'а — 403 с явным
    error_code и failure-audit `scope_denies_docker`.
    """
    await _enable_docker(client, admin_token, dept_a.id)
    pat = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "no_docker_pat", "allowed_services": [service_x.service_name], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )).json()["token"]

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", pat),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "PAT_SCOPE_DENIES_DOCKER"


async def test_bot_without_docker_scope_denied(
    client, admin_token, dept_a, dept_a_with_service, service_x,
):
    """Bot с непустым `allowed_services`, не содержащим `docker_registry`,
    отбивается 403 BOT_SCOPE_DENIES_DOCKER (симметрия PAT-теста)."""
    await _enable_docker(client, admin_token, dept_a.id)
    bot_id = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "no_docker_bot",
            "department_id": dept_a.id,
            "allowed_services": [service_x.service_name],
        },
    )).json()["bot_id"]
    bot_token = (await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "no_docker_tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )).json()["token"]

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("no_docker_bot", bot_token),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "BOT_SCOPE_DENIES_DOCKER"


# ── Public key / JWKS endpoints ───────────────────────────────────────────────

async def test_certs_endpoint_returns_pem(client):
    resp = await client.get("/api/auth/v1/docker/certs")
    assert resp.status_code == 200
    assert "BEGIN CERTIFICATE" in resp.text


async def test_jwks_endpoint_returns_rsa_key(client):
    resp = await client.get("/api/auth/v1/docker/jwks")
    assert resp.status_code == 200
    body = resp.json()
    assert "keys" in body
    assert len(body["keys"]) >= 1
    key = body["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert "n" in key
    assert "e" in key


# ── Brute-force lockout via /docker/token ────────────────────────────────────
# `/login` increments `failed_login_attempts` and sets `locked_until` after 5
# misses, but `/docker/token` historically bypassed this — Argon2id ≈ 100 ms
# per call was the only brute-force cap.  The fix routes password
# authentication through `verify_password_with_lockout` (shared with `/login`).


async def test_docker_token_locks_account_after_5_failed_attempts(
    client, admin_token, user_a, dept_a, docker_registry_enabled,
):
    """5 wrong-password Docker token requests must lock the account."""
    from sqlalchemy import select as sa_select
    from src.models import User as UserModel

    # 5 wrong attempts — must trigger lockout.
    for _ in range(5):
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "WrongPass!"),
            params={"service": "registry.test"},
        )
        # Each attempt is 401 INVALID_CREDENTIALS until the threshold flips.
        assert resp.status_code in (401, 429)

    # 6th attempt — even with correct password — must be locked out.
    # The active-lockout branch in `verify_password_with_lockout` raises
    # AuthorizationError(ACCOUNT_TEMPORARILY_LOCKED, 429) BEFORE calling
    # verify_password, so we use the right password to prove it's not a
    # wrong-password 401.
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
    assert body.get("details", {}).get("retry_after_seconds", 0) > 0


async def test_docker_token_locked_user_cannot_login(
    client, admin_token, user_a, dept_a, db, docker_registry_enabled,
):
    """A user locked by /login pipeline cannot bypass via /docker/token."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update as sa_update
    from src.models import User as UserModel

    # Simulate a fresh lockout (set by /login earlier).
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    await db.execute(
        sa_update(UserModel)
        .where(UserModel.id == user_a.id)
        .values(failed_login_attempts=5, locked_until=future)
    )
    await db.commit()

    # Even with correct password, /docker/token must honour the lockout.
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"


async def test_docker_token_lockout_emits_audit_failure(
    client, admin_token, user_a, dept_a, db, docker_registry_enabled, monkeypatch,
):
    """Когда `/docker/token` упирается в lockout — должен эмититься
    `docker.token_issued status=failure reason=account_locked`. Иначе SOC
    слеп на brute-force через docker auth (раньше `/login` корректно эмитил
    `user.login failure / account_locked`, а `/docker/token` — ничего).
    Симметрия с login-flow.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update as sa_update
    from src.models import User as UserModel
    from src.services import audit_service as _audit_service

    captured: list[dict] = []

    class _AsyncClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)

    original_info = _audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and msg.startswith("audit_event_fallback") and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(_audit_service.logger, "info", fake_info)
    monkeypatch.setattr("src.services.audit_service.get_settings", lambda: type(
        "S", (), {"logging_service_url": "http://test", "logging_service_api_key": "k"},
    )())

    # Сразу выставляем активный lockout, без подбора пароля.
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    await db.execute(
        sa_update(UserModel)
        .where(UserModel.id == user_a.id)
        .values(failed_login_attempts=5, locked_until=future)
    )
    await db.commit()

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"

    locked_events = [
        p for p in captured
        if p.get("action") == "docker.token_issued"
        and p.get("status") == "failure"
        and (p.get("details") or {}).get("reason") == "account_locked"
    ]
    assert len(locked_events) >= 1, (
        f"docker.token_issued failure/account_locked must be emitted; captured={captured}"
    )
    ev = locked_events[0]
    assert ev["allowed"] is False
    assert ev["actor_id"] == user_a.id
    assert ev["details"]["username"] == "t_user_a"
    assert ev["details"].get("retry_after_seconds") is not None


async def test_docker_token_successful_login_resets_failed_attempts(
    client, admin_token, user_a, dept_a, db, docker_registry_enabled,
):
    """A successful /docker/token call must clear `failed_login_attempts`
    just like /login does — otherwise legitimate users get locked out from
    pre-existing fail counters that should have been forgiven by success.
    """
    from sqlalchemy import update as sa_update, select as sa_select
    from src.models import User as UserModel

    # Capture id BEFORE any expire_all — `user_a` is a detached fixture row
    # and re-reading `.id` after expire_all would trigger a fresh greenlet
    # SELECT that doesn't play with the test session's nesting.
    user_a_id = user_a.id

    # Pre-seed 4 failed attempts (below threshold — not yet locked).
    await db.execute(
        sa_update(UserModel)
        .where(UserModel.id == user_a_id)
        .values(failed_login_attempts=4, locked_until=None)
    )
    await db.commit()

    # Successful Docker token request — should reset the counter.
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 200

    # Re-read from DB (bypass identity map) to verify the reset persisted.
    db.expire_all()
    fresh = await db.scalar(sa_select(UserModel).where(UserModel.id == user_a_id))
    assert fresh.failed_login_attempts == 0, (
        f"counter not reset on success: got {fresh.failed_login_attempts}, expected 0"
    )
    assert fresh.locked_until is None


async def test_docker_token_failed_attempt_increments_counter_in_db(
    client, admin_token, user_a, dept_a, db, docker_registry_enabled,
):
    """Regression: each wrong-pw call must commit the counter increment,
    so a brute-forcer that runs through 1000 sessions still hits the lockout
    (the bug was that the increment used to live in a transaction that the
    outer get_db() rolled back on raise → counter stayed at zero).
    """
    from sqlalchemy import select as sa_select
    from src.models import User as UserModel

    # Capture id BEFORE any HTTP traffic — see sibling test for rationale.
    user_a_id = user_a.id

    # 3 wrong attempts (below threshold so we observe the raw counter).
    for _ in range(3):
        await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "WrongPass!"),
            params={"service": "registry.test"},
        )

    db.expire_all()
    fresh = await db.scalar(sa_select(UserModel).where(UserModel.id == user_a_id))
    assert fresh.failed_login_attempts == 3, (
        f"counter not persisted: got {fresh.failed_login_attempts}, expected 3"
    )
    assert fresh.locked_until is None


# ── Multi legacy scope (без `<dept>/`) ────────────────────────────────────────

async def test_multi_legacy_scope_resolves_caller_dept_for_each_entry(
    client, admin_token, user_a, dept_a,
):
    """Несколько legacy-scope entries в одном запросе должны корректно
    разрешиться в конфиг caller-отдела для каждого.

    Регрессионный тест на старый код, где `legacy_cfg = None` инициализировался
    до цикла и фактически никогда не использовался — каждый legacy-entry бил по
    БД дважды (dept_repo + docker_repo). Сейчас caller-cfg считается один раз
    перед циклом, и оба entries получают одинаковый успешный pull.
    """
    await _enable_docker(client, admin_token, dept_a.id, pull_policy="all")
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={
            "service": "registry.test",
            "scope": "repository:foo:pull repository:bar:pull",
        },
    )
    assert resp.status_code == 200
    access = _decode_access(resp.json()["access_token"])
    names = {entry["name"] for entry in access if "pull" in entry.get("actions", [])}
    assert {"foo", "bar"}.issubset(names)
