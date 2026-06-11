"""Тесты: /api/auth/v1/departments — управление отделами и доступом к сервисам."""

CREATE_URL = "/api/auth/v1/departments"
GRANT_URL = "/api/auth/v1/departments/{dept_id}/services"
REVOKE_URL = "/api/auth/v1/departments/{dept_id}/services/{svc_name}"


# ── Create department ─────────────────────────────────────────────────────────

async def test_admin_creates_department(client, admin_token):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "finance"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "finance"


async def test_duplicate_dept_name_returns_409(client, admin_token, dept_a):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": dept_a.name})
    assert resp.status_code == 409


async def test_dept_admin_cannot_create_department(client, dept_admin_a_token):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"name": "new_dept"})
    assert resp.status_code == 403


async def test_list_departments_returns_active(client, admin_token, dept_a, dept_b):
    resp = await client.get(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()]
    assert dept_a.name in names
    assert dept_b.name in names


# ── Grant / revoke service access ─────────────────────────────────────────────

async def test_admin_grants_service_to_dept(client, admin_token, dept_b, service_x):
    url = GRANT_URL.format(dept_id=dept_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 201


async def test_duplicate_grant_returns_409(client, admin_token, dept_a_with_service, service_x):
    url = GRANT_URL.format(dept_id=dept_a_with_service.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 409


async def test_grant_nonexistent_service_returns_404(client, admin_token, dept_a):
    url = GRANT_URL.format(dept_id=dept_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": "does_not_exist"})
    assert resp.status_code == 404


async def test_dept_admin_cannot_grant_service(client, dept_admin_a_token, dept_a, service_x):
    url = GRANT_URL.format(dept_id=dept_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 403


async def test_admin_revokes_service_from_dept(client, admin_token, dept_a_with_service, service_x):
    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    resp = await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_after_revoke_user_login_loses_service(client, admin_token, user_a, user_a_token,
                                                      dept_a_with_service, service_x):
    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post("/api/auth/v1/login", json={"username": "t_user_a", "password": "User12345678!"})
    assert service_x.service_name not in resp.json()["identity"]["allowed_services"]


# ── department.service_grant audit: reactivated-flag должен отличать первое
#    выделение от реактивации revoked access ──────────────────────────────────


# `capture_audit_payloads` — общая фикстура из `tests/conftest.py`.


async def test_first_grant_audit_has_reactivated_false(
    client, admin_token, dept_b, service_x, capture_audit_payloads,
):
    url = GRANT_URL.format(dept_id=dept_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 201
    grants = [p for p in capture_audit_payloads if p["action"] == "department.service_grant"]
    assert grants, "audit-событие должно эмититься"
    assert grants[-1]["details"]["reactivated"] is False


async def test_regrant_after_revoke_audit_has_reactivated_true(
    client, admin_token, dept_a_with_service, service_x, capture_audit_payloads,
):
    """revoke + повторный grant даёт `reactivated=True` в audit-events."""
    revoke = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    r = await client.delete(revoke, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200

    capture_audit_payloads.clear()
    grant = GRANT_URL.format(dept_id=dept_a_with_service.id)
    r = await client.post(grant, headers={"Authorization": f"Bearer {admin_token}"},
                           json={"service_name": service_x.service_name})
    assert r.status_code == 201

    grants = [p for p in capture_audit_payloads if p["action"] == "department.service_grant"]
    assert grants, "audit-событие должно эмититься"
    assert grants[-1]["details"]["reactivated"] is True


# ── revoke_service_access должен сбрасывать identity-cache затронутых юзеров ──


async def test_revoke_invalidates_identity_cache_for_role_holders(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x, monkeypatch,
):
    """После revoke сервиса идентичность юзера с ролью на этом сервисе
    должна быть сброшена в кэше, чтобы revoke действовал мгновенно (не по TTL)."""
    invalidated: list[str] = []

    def _spy(uid):
        invalidated.append(uid)

    monkeypatch.setattr(
        "src.services.department_service._invalidate_identity_cache", _spy
    )

    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    resp = await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert user_a.id in invalidated, (
        f"identity-cache юзера с прямой ролью должен быть сброшен, got {invalidated}"
    )


async def test_revoke_audit_includes_affected_user_count(
    client, admin_token, user_a, dept_a_with_service, service_x, capture_audit_payloads,
):
    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    resp = await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    revokes = [p for p in capture_audit_payloads if p["action"] == "department.service_revoke"]
    assert revokes, "audit-событие должно эмититься"
    assert revokes[-1]["details"].get("affected_user_count", 0) >= 1


async def test_revoke_cascades_group_service_access(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x, db,
):
    """revoke сервиса каскадно снимает `GroupServiceAccess` отдела.

    Без каскада `_merge_permissions` через `list_active_services_by_groups`
    продолжал бы возвращать revoked-сервис юзеру (`allowed_services` через
    group-канал) и downstream-сервисы пускали бы по stale scope.
    """
    GROUPS_URL = "/api/auth/v1/groups"
    # Создаём группу в отделе, выдаём ей доступ к сервису, кладём юзера.
    grp_resp = await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"department_id": dept_a_with_service.id, "name": "rgrp"},
    )
    assert grp_resp.status_code == 201, grp_resp.text
    group_id = grp_resp.json()["id"]

    grant_resp = await client.post(
        f"{GROUPS_URL}/{group_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    add_resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )
    assert add_resp.status_code in (200, 201), add_resp.text

    # До revoke — service_x присутствует в allowed_services через group-канал.
    me_before = await client.get(
        "/api/auth/v1/me",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert me_before.status_code == 200
    assert service_x.service_name in me_before.json()["allowed_services"]

    # Revoke сервиса у отдела.
    rurl = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    rresp = await client.delete(rurl, headers={"Authorization": f"Bearer {admin_token}"})
    assert rresp.status_code == 200, rresp.text

    # group_service_access должен быть деактивирован — service_x ушёл из allowed_services.
    from sqlalchemy import select
    from src.models.group_service_access import GroupServiceAccess
    row = await db.scalar(
        select(GroupServiceAccess).where(
            GroupServiceAccess.group_id == group_id,
            GroupServiceAccess.service_name == service_x.service_name,
        )
    )
    assert row is not None
    assert row.is_active is False, "GroupServiceAccess должен каскадно сняться"

    me_after = await client.get(
        "/api/auth/v1/me",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert me_after.status_code == 200
    assert service_x.service_name not in me_after.json()["allowed_services"], (
        "revoked-сервис не должен оставаться в allowed_services через group-канал"
    )
