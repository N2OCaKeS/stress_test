"""HTTP-эндпоинты /credentials/{id}/acl — add/list/revoke."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo


OWNER_USER_ID = "usr_owner000000000000000000000001"
OWNER_DEPT = "dep_owner00000000000000000000001"
RECIPIENT_DEPT = "dep_recip00000000000000000000001"


def _identity(
    *,
    user_id: str = OWNER_USER_ID,
    department_id: str | None = OWNER_DEPT,
    roles: list[str] | None = None,
    platform_role: str | None = "department_admin",
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or ["admin"]},
        is_banned=False,
        platform_role=platform_role,
    )


@pytest_asyncio.fixture
async def http_client(adb):
    async def _get_db_override():
        yield adb

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_identity] = lambda: _identity()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _set_identity(identity: Identity) -> None:
    app.dependency_overrides[get_identity] = lambda: identity


@pytest_asyncio.fixture
async def dept_cred(adb):
    """Готовая department-cred внутри owner-dep'а."""
    cred = await cred_repo.create(
        adb,
        id="cred_dep_a01",
        name="dep_jira",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id=OWNER_DEPT,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=OWNER_USER_ID,
    )
    await adb.commit()
    return cred


@pytest_asyncio.fixture
async def cross_dep_cred(adb):
    cred = await cred_repo.create(
        adb,
        id="cred_xdep_a01",
        name="xdep_jira",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id=OWNER_DEPT,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=OWNER_USER_ID,
    )
    await adb.commit()
    return cred


# ── ADD ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_acl_dept_cred_ok(http_client, dept_cred):
    payload = {
        "dept_id": OWNER_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl", json=payload
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dept_id"] == OWNER_DEPT
    assert body["role_name"] == "reader"
    assert body["can_read"] is True
    assert body["granted_by_user_id"] == OWNER_USER_ID


@pytest.mark.asyncio
async def test_add_acl_cross_dep_without_dept_grant_rejected(http_client, cross_dep_cred):
    payload = {
        "dept_id": RECIPIENT_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    # Local recipient dep_admin пытается выдать ACL без DeptGrant.
    _set_identity(_identity(
        department_id=RECIPIENT_DEPT,
        platform_role="department_admin",
    ))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl", json=payload
    )
    # cross_dep recipient без DeptGrant — visibility miss, 404 (info-leak).
    # 422 принимаем как валидный вариант, если эндпоинт валидирует DEPT_GRANT_REQUIRED
    # до access-check.
    assert resp.status_code in (404, 422)


@pytest.mark.asyncio
async def test_add_acl_cross_dep_with_dept_grant_ok(http_client, cross_dep_cred, adb):
    # Сначала owner dep_admin создаёт DeptGrant.
    await grants_repo.create(
        adb,
        id="dgr_test1",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=RECIPIENT_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()

    # Recipient dep_admin создаёт ACL.
    _set_identity(_identity(
        department_id=RECIPIENT_DEPT,
        platform_role="department_admin",
    ))
    payload = {
        "dept_id": RECIPIENT_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl", json=payload
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_add_acl_duplicate_409(http_client, dept_cred):
    payload = {
        "dept_id": OWNER_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    r1 = await http_client.post(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl", json=payload
    )
    assert r1.status_code == 201
    r2 = await http_client.post(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl", json=payload
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_acl_not_found(http_client):
    payload = {
        "dept_id": OWNER_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    resp = await http_client.post(
        "/api/secret/v1/credentials/cred_missing/acl", json=payload
    )
    assert resp.status_code == 404


# ── LIST ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_acls(http_client, dept_cred, adb):
    await acls_repo.create(
        adb,
        id="acl_lst1",
        cred_id=dept_cred.id,
        dept_id=OWNER_DEPT,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    resp = await http_client.get(f"/api/secret/v1/credentials/{dept_cred.id}/acl")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1


@pytest.mark.asyncio
async def test_list_acls_cred_not_found(http_client):
    resp = await http_client.get("/api/secret/v1/credentials/cred_missing/acl")
    assert resp.status_code == 404


# ── REVOKE ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_acl_ok(http_client, dept_cred, adb):
    acl = await acls_repo.create(
        adb,
        id="acl_rv1",
        cred_id=dept_cred.id,
        dept_id=OWNER_DEPT,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl/{acl.id}"
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_revoke_acl_not_found(http_client, dept_cred):
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl/acl_missing"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_acl_by_reader_denied(http_client, dept_cred, adb):
    acl = await acls_repo.create(
        adb,
        id="acl_rv2",
        cred_id=dept_cred.id,
        dept_id=OWNER_DEPT,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    # Снимаем admin-роль, делаем простого reader'а в чужом dep'е.
    _set_identity(_identity(
        user_id="usr_otheruser0000000000000000001",
        department_id="dep_other00000000000000000000001",
        roles=["reader"],
        platform_role=None,
    ))
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl/{acl.id}"
    )
    assert resp.status_code in (403, 404)
