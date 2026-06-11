"""Тесты: /api/auth/v1/bots — сервисные боты и их токены."""

from datetime import timedelta

from src.utils.time import utcnow
from tests._helpers.http import _create_bot  # noqa: F401 — общий helper

BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


def _exp(days: int = 30) -> str:
    return (utcnow() + timedelta(days=days)).isoformat()


# ── Create ────────────────────────────────────────────────────────────────────

async def test_admin_creates_bot(client, admin_token, dept_a):
    resp = await _create_bot(client, admin_token, dept_a.id)
    assert resp.status_code == 201
    assert resp.json()["department_id"] == dept_a.id


async def test_dept_admin_creates_bot_in_own_dept(client, dept_admin_a_token, dept_a):
    resp = await _create_bot(client, dept_admin_a_token, dept_a.id, name="own_bot")
    assert resp.status_code == 201


async def test_dept_admin_cannot_create_bot_in_other_dept(client, dept_admin_a_token, dept_b):
    resp = await _create_bot(client, dept_admin_a_token, dept_b.id, name="cross_bot")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_CREATION_FORBIDDEN"


async def test_bot_with_dept_services_allowed(client, admin_token, dept_a_with_service, service_x):
    resp = await _create_bot(client, admin_token, dept_a_with_service.id,
                              services=[service_x.service_name])
    assert resp.status_code == 201


async def test_bot_with_service_not_in_dept_returns_403(client, admin_token, dept_b, service_x):
    """service_x is not granted to dept_b → creating bot with it should fail."""
    resp = await _create_bot(client, admin_token, dept_b.id, services=[service_x.service_name])
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"


async def test_regular_user_cannot_create_bot(client, user_a_token, dept_a):
    resp = await _create_bot(client, user_a_token, dept_a.id, name="hacker_bot")
    assert resp.status_code == 403


async def test_duplicate_name_in_same_dept_returns_409(client, admin_token, dept_a):
    name = "dup_bot_same_dept"
    first = await _create_bot(client, admin_token, dept_a.id, name=name)
    assert first.status_code == 201
    second = await _create_bot(client, admin_token, dept_a.id, name=name)
    assert second.status_code == 409
    assert second.json()["error_code"] == "BOT_NAME_TAKEN"


async def test_duplicate_name_across_depts_returns_409(client, admin_token, dept_a, dept_b):
    """bot.name глобально-уникален: коллизия даже между разными отделами → 409."""
    name = "dup_bot_cross_dept"
    first = await _create_bot(client, admin_token, dept_a.id, name=name)
    assert first.status_code == 201
    second = await _create_bot(client, admin_token, dept_b.id, name=name)
    assert second.status_code == 409
    assert second.json()["error_code"] == "BOT_NAME_TAKEN"


# ── List ──────────────────────────────────────────────────────────────────────

async def test_admin_sees_all_bots(client, admin_token, dept_a, dept_b):
    await _create_bot(client, admin_token, dept_a.id, name="bot_a1")
    await _create_bot(client, admin_token, dept_b.id, name="bot_b1")
    resp = await client.get(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [b["name"] for b in resp.json()]
    assert "bot_a1" in names
    assert "bot_b1" in names


async def test_dept_admin_sees_only_own_dept_bots(client, dept_admin_a_token, admin_token, dept_a, dept_b):
    await _create_bot(client, admin_token, dept_a.id, name="bot_a_only")
    await _create_bot(client, admin_token, dept_b.id, name="bot_b_hidden")
    resp = await client.get(BOTS_URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    names = [b["name"] for b in resp.json()]
    assert "bot_a_only" in names
    assert "bot_b_hidden" not in names


# ── Bot tokens ────────────────────────────────────────────────────────────────

async def test_create_bot_token(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="tok_bot")).json()["bot_id"]
    resp = await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "ci_token", "expires_at": _exp()})
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"].startswith("dbos_bot_")


async def test_bot_token_works_in_introspect(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="intr_bot")).json()["bot_id"]
    raw_token = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                                    headers={"Authorization": f"Bearer {admin_token}"},
                                    json={"name": "intr_tok", "expires_at": _exp()})).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert resp.json()["active"] is True
    assert resp.json()["subject_type"] == "bot"


async def test_revoked_bot_token_inactive_in_introspect(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="rev_bot")).json()["bot_id"]
    tok_data = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                                   headers={"Authorization": f"Bearer {admin_token}"},
                                   json={"name": "rev_tok", "expires_at": _exp()})).json()
    await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_data['token_id']}",
                        headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post(INTROSPECT_URL, json={"token": tok_data["token"]})
    assert resp.json()["active"] is False


# ── PATCH bot ─────────────────────────────────────────────────────────────────


async def test_admin_updates_bot_description(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="patch_bot")).json()["bot_id"]
    resp = await client.patch(f"{BOTS_URL}/{bot_id}",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"description": "обновлённое описание"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "обновлённое описание"


async def test_admin_updates_bot_status(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="status_bot")).json()["bot_id"]
    resp = await client.patch(f"{BOTS_URL}/{bot_id}",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"status": "disabled"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


async def test_dept_admin_a_cannot_update_bot_in_dept_b(client, dept_admin_a_token, admin_token, dept_b):
    bot_id = (await _create_bot(client, admin_token, dept_b.id, name="cross_patch_bot")).json()["bot_id"]
    resp = await client.patch(f"{BOTS_URL}/{bot_id}",
                              headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"description": "hijack"})
    assert resp.status_code == 403


# ── List bot tokens ───────────────────────────────────────────────────────────


async def test_list_bot_tokens_excludes_raw_secret(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="list_tok_bot")).json()["bot_id"]
    raw = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "ls_tok", "expires_at": _exp()})).json()["token"]
    resp = await client.get(f"{BOTS_URL}/{bot_id}/tokens",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    for it in items:
        assert "token" not in it
        assert "token_hash" not in it
        assert it.get("token_prefix") and not it["token_prefix"].endswith(raw[12:])


# ── Revoke bot token ──────────────────────────────────────────────────────────


async def test_revoke_bot_token_returns_200(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="rev200_bot")).json()["bot_id"]
    tok_id = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                                 headers={"Authorization": f"Bearer {admin_token}"},
                                 json={"name": "to_kill", "expires_at": _exp()})).json()["token_id"]
    resp = await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_id}",
                               headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_double_revoke_bot_token_returns_409(client, admin_token, dept_a):
    bot_id = (await _create_bot(client, admin_token, dept_a.id, name="rev409_bot")).json()["bot_id"]
    tok_id = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                                 headers={"Authorization": f"Bearer {admin_token}"},
                                 json={"name": "dbl_kill", "expires_at": _exp()})).json()["token_id"]
    await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_id}",
                        headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_id}",
                               headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 409


# ── Scope (existing) ──────────────────────────────────────────────────────────


async def test_bot_token_scope_limited_by_dept_access(client, admin_token, dept_a_with_service, service_x):
    """After dept service revoke, bot token introspect should no longer include the service."""
    bot_id = (await _create_bot(client, admin_token, dept_a_with_service.id,
                                 services=[service_x.service_name])).json()["bot_id"]
    raw_token = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                                    headers={"Authorization": f"Bearer {admin_token}"},
                                    json={"name": "scope_tok", "expires_at": _exp()})).json()["token"]
    await client.delete(f"/api/auth/v1/departments/{dept_a_with_service.id}/services/{service_x.service_name}",
                        headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert service_x.service_name not in resp.json().get("allowed_services", [])


# ── actor_department_id shortcut: skip extra SELECT при known identity ───────


async def test_resolve_actor_dept_uses_provided_value_without_select(db, dept_a, monkeypatch):
    """Когда endpoint прокинул actor_department_id из IdentityContext —
    хелпер не должен дёргать UserRepository.get_by_id (лишний SELECT)."""
    from src.repositories import users as users_module
    from src.services import bot_service

    calls = {"count": 0}
    original = users_module.UserRepository.get_by_id

    async def spy(self, user_id):  # pragma: no cover — мониторинг
        calls["count"] += 1
        return await original(self, user_id)

    monkeypatch.setattr(users_module.UserRepository, "get_by_id", spy)

    dept_id = await bot_service._resolve_actor_dept(db, "usr_some_actor", dept_a.id)
    assert dept_id == dept_a.id
    assert calls["count"] == 0


async def test_resolve_actor_dept_fallbacks_to_select_when_missing(
    db, account_admin, monkeypatch,
):
    """Если actor_department_id=None — фолбэк на SELECT (для legacy-callers
    и прямых вызовов из других сервисов, не через endpoint)."""
    from src.repositories import users as users_module
    from src.services import bot_service

    calls = {"count": 0}
    original = users_module.UserRepository.get_by_id

    async def spy(self, user_id):
        calls["count"] += 1
        return await original(self, user_id)

    monkeypatch.setattr(users_module.UserRepository, "get_by_id", spy)

    # account_admin фикстурно имеет department_id=None, но get_by_id обязан быть вызван.
    await bot_service._resolve_actor_dept(db, account_admin.id, None)
    assert calls["count"] == 1
