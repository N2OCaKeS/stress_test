"""Тесты: PUT/DELETE /api/auth/v1/docker/registry/{dept_id} — настройка Docker registry для отдела."""

import base64

CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
TOKEN_URL = "/api/auth/v1/docker/token"


def _basic(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


# ── Create / replace config ───────────────────────────────────────────────────

async def test_admin_enables_docker_for_dept(client, admin_token, dept_a):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"pull_policy": "all", "push_user_ids": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_enabled"] is True
    assert body["pull_policy"] == "all"


async def test_dept_admin_enables_docker_for_own_dept(client, dept_admin_a_token, dept_a):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                      json={"pull_policy": "all", "push_user_ids": []})
    assert resp.status_code == 200


async def test_dept_admin_cannot_enable_docker_for_other_dept(client, dept_admin_a_token, dept_b):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_b.id),
                      headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                      json={"pull_policy": "all", "push_user_ids": []})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


async def test_restricted_policy_without_users_returns_error(client, admin_token, dept_a):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"pull_policy": "restricted", "pull_user_ids": [], "push_user_ids": []})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PULL_USERS_REQUIRED"


async def test_restricted_policy_with_users_succeeds(client, admin_token, dept_a, user_a):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"pull_policy": "restricted", "pull_user_ids": [user_a.id],
                            "push_user_ids": []})
    assert resp.status_code == 200


async def test_regular_user_cannot_configure_docker(client, user_a_token, dept_a):
    resp = await client.put(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      json={"pull_policy": "all", "push_user_ids": []})
    assert resp.status_code == 403


# ── Get config ────────────────────────────────────────────────────────────────

async def test_get_docker_config(client, admin_token, docker_registry_enabled, dept_a):
    resp = await client.get(CONFIG_URL.format(dept_id=dept_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["is_enabled"] is True


async def test_get_config_not_configured_returns_404(client, admin_token, dept_b):
    resp = await client.get(CONFIG_URL.format(dept_id=dept_b.id),
                      headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


# ── Update config ─────────────────────────────────────────────────────────────

async def test_update_docker_config(client, admin_token, docker_registry_enabled, dept_a, user_a):
    user_id = user_a.id  # read before HTTP request to avoid lazy-load outside greenlet
    resp = await client.patch(CONFIG_URL.format(dept_id=dept_a.id),
                        headers={"Authorization": f"Bearer {admin_token}"},
                        json={"push_user_ids": [user_id]})
    assert resp.status_code == 200
    assert user_id in resp.json()["push_user_ids"]


# ── Disable (DELETE) ──────────────────────────────────────────────────────────

async def test_disable_docker_registry(client, admin_token, docker_registry_enabled, dept_a):
    resp = await client.delete(CONFIG_URL.format(dept_id=dept_a.id),
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    get_resp = await client.get(CONFIG_URL.format(dept_id=dept_a.id),
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert get_resp.json()["is_enabled"] is False
