"""Тесты: GET/PUT /api/auth/v1/admin/lockout-policy — runtime-override lockout-политики.

GET без БД-строки отдаёт env-дефолты (source=env). PUT (account_admin) пишет
override (source=db) и эмитит `lockout_policy.update`. Валидация отбивает 0/negative.
department_admin — 403 на обоих. Резолвер предпочитает БД env'у (unit).
"""

import pytest

from src.core.config import get_settings
from src.services.audit_events import SERVICE_EVENTS

POLICY_URL = "/api/auth/v1/admin/lockout-policy"


def test_lockout_policy_event_severity_is_critical():
    ev = next(e for e in SERVICE_EVENTS if e["action"] == "lockout_policy.update")
    assert ev["default_severity"] == "CRITICAL"


# ── GET defaults / PUT roundtrip ────────────────────────────────────────────────

async def test_get_returns_env_defaults_when_no_row(client, admin_token):
    resp = await client.get(POLICY_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    settings = get_settings()
    assert body["source"] == "env"
    assert body["max_failed_attempts"] == settings.max_failed_login_attempts
    assert body["lockout_minutes"] == settings.lockout_minutes


async def test_put_then_get_returns_db_values(client, admin_token):
    put = await client.put(
        POLICY_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_failed_attempts": 3, "lockout_minutes": 7},
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"max_failed_attempts": 3, "lockout_minutes": 7, "source": "db"}

    get = await client.get(POLICY_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert get.status_code == 200, get.text
    body = get.json()
    assert body["source"] == "db"
    assert body["max_failed_attempts"] == 3
    assert body["lockout_minutes"] == 7


async def test_put_is_idempotent_upsert(client, admin_token):
    for max_a, mins in ((4, 10), (2, 30)):
        put = await client.put(
            POLICY_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"max_failed_attempts": max_a, "lockout_minutes": mins},
        )
        assert put.status_code == 200, put.text
    get = await client.get(POLICY_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert get.json()["max_failed_attempts"] == 2
    assert get.json()["lockout_minutes"] == 30


async def test_put_emits_audit(client, admin_token, capture_audit_payloads):
    await client.put(
        POLICY_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_failed_attempts": 6, "lockout_minutes": 20},
    )
    actions = [p["action"] for p in capture_audit_payloads]
    assert "lockout_policy.update" in actions
    ev = next(p for p in capture_audit_payloads if p["action"] == "lockout_policy.update")
    assert ev["details"]["new_max"] == 6
    assert ev["details"]["new_minutes"] == 20


# ── validation ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "payload",
    [
        {"max_failed_attempts": 0, "lockout_minutes": 5},
        {"max_failed_attempts": 5, "lockout_minutes": 0},
        {"max_failed_attempts": -1, "lockout_minutes": 5},
        {"max_failed_attempts": 5, "lockout_minutes": -3},
    ],
)
async def test_put_rejects_non_positive(client, admin_token, payload):
    resp = await client.put(
        POLICY_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert resp.status_code == 422, resp.text


# ── authz ─────────────────────────────────────────────────────────────────────

async def test_dept_admin_forbidden_get(client, dept_admin_a_token):
    resp = await client.get(POLICY_URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


async def test_dept_admin_forbidden_put(client, dept_admin_a_token):
    resp = await client.put(
        POLICY_URL,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"max_failed_attempts": 3, "lockout_minutes": 7},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


# ── resolver: DB beats env ──────────────────────────────────────────────────────

async def test_resolver_prefers_db_over_env(db, admin_token, client):
    from src.repositories.lockout_policy import LockoutPolicyRepository
    from src.services.lockout_policy_service import resolve_lockout_policy

    settings = get_settings()
    # Без строки — env.
    max_a, mins = await resolve_lockout_policy(db)
    assert (max_a, mins) == (settings.max_failed_login_attempts, settings.lockout_minutes)

    # Со строкой — БД.
    await LockoutPolicyRepository(db).upsert(
        max_failed_attempts=settings.max_failed_login_attempts + 2,
        lockout_minutes=settings.lockout_minutes + 5,
        updated_by="usr_test",
    )
    await db.commit()
    max_a, mins = await resolve_lockout_policy(db)
    assert max_a == settings.max_failed_login_attempts + 2
    assert mins == settings.lockout_minutes + 5
