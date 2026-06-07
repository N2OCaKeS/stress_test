"""HTTP-эндпоинты /credentials/{id}/dept-grants — add/list/revoke + cascade."""

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
async def cross_dep_cred(adb):
    cred = await cred_repo.create(
        adb,
        id="cred_dg_x01",
        name="dg_jira",
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


@pytest_asyncio.fixture
async def dept_cred(adb):
    cred = await cred_repo.create(
        adb,
        id="cred_dg_d01",
        name="dg_dept",
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


# ── ADD ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dept_grant_ok(http_client, cross_dep_cred):
    payload = {"recipient_dept_id": RECIPIENT_DEPT}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants", json=payload
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["recipient_dept_id"] == RECIPIENT_DEPT
    assert body["granted_by_user_id"] == OWNER_USER_ID


@pytest.mark.asyncio
async def test_add_dept_grant_on_non_cross_dep_rejected(http_client, dept_cred):
    payload = {"recipient_dept_id": RECIPIENT_DEPT}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{dept_cred.id}/dept-grants", json=payload
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "DEPT_GRANT_NOT_APPLICABLE"


@pytest.mark.asyncio
async def test_add_dept_grant_duplicate_409(http_client, cross_dep_cred):
    payload = {"recipient_dept_id": RECIPIENT_DEPT}
    r1 = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants", json=payload
    )
    assert r1.status_code == 201
    r2 = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants", json=payload
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_dept_grant_recipient_eq_owner_rejected(http_client, cross_dep_cred):
    payload = {"recipient_dept_id": OWNER_DEPT}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants", json=payload
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "DEPT_GRANT_RECIPIENT_IS_OWNER"


@pytest.mark.asyncio
async def test_add_dept_grant_cred_not_found(http_client):
    payload = {"recipient_dept_id": RECIPIENT_DEPT}
    resp = await http_client.post(
        "/api/secret/v1/credentials/cred_missing/dept-grants", json=payload
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_dept_grant_by_non_owner_dep_denied(http_client, cross_dep_cred):
    # Перенесём identity в другой dep, без admin-роли.
    _set_identity(_identity(
        user_id="usr_x0000000000000000000000000001",
        department_id="dep_unrelated0000000000000000001",
        roles=["operator"],
        platform_role="department_admin",
    ))
    payload = {"recipient_dept_id": RECIPIENT_DEPT}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants", json=payload
    )
    assert resp.status_code in (403, 404)


# ── LIST ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dept_grants(http_client, cross_dep_cred, adb):
    await grants_repo.create(
        adb,
        id="dgr_list1",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=RECIPIENT_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    resp = await http_client.get(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1


@pytest.mark.asyncio
async def test_list_dept_grants_non_cross_dep_rejected(http_client, dept_cred):
    resp = await http_client.get(
        f"/api/secret/v1/credentials/{dept_cred.id}/dept-grants"
    )
    assert resp.status_code == 422


# ── REVOKE + CASCADE ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_dept_grant_cascades_role_acls(http_client, cross_dep_cred, adb):
    grant = await grants_repo.create(
        adb,
        id="dgr_rv1",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=RECIPIENT_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    # Параллельно вешаем ACL'и на recipient_dept
    await acls_repo.create(
        adb,
        id="acl_csc1",
        cred_id=cross_dep_cred.id,
        dept_id=RECIPIENT_DEPT,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await acls_repo.create(
        adb,
        id="acl_csc2",
        cred_id=cross_dep_cred.id,
        dept_id=RECIPIENT_DEPT,
        role_name="operator",
        can_read=True,
        can_write=True,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()

    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants/{grant.id}"
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Проверяем cascade — оба ACL'я в recipient_dept должны исчезнуть.
    remaining = await acls_repo.get_for_cred_dept(
        adb, cross_dep_cred.id, RECIPIENT_DEPT
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_revoke_dept_grant_not_found(http_client, cross_dep_cred):
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/dept-grants/dgr_missing"
    )
    assert resp.status_code == 404
