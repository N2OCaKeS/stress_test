"""HTTP-эндпоинты /credentials/{id}/user-acl — add/list/revoke + доступ grantee.

Real Postgres + ASGI client; identity мокается через override зависимостей.
"""

from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app
from src.repositories import credentials as cred_repo
from src.repositories import user_acls as uacl_repo
from src.services import reveal_throttle
from src.services import secrets_service
from tests._helpers import b64


OWNER_USER_ID = "usr_owner000000000000000000000001"
OWNER_DEPT = "dep_owner00000000000000000000001"
GRANTEE_ID = "usr_grantee00000000000000000001"
GRANTEE_DEPT = "dep_grantee0000000000000000001"
OUTSIDER_ID = "usr_outsider0000000000000000001"


def _identity(
    *,
    user_id: str = OWNER_USER_ID,
    actor_type: str = "user",
    department_id: str | None = OWNER_DEPT,
    roles: list[str] | None = None,
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or ["operator"]},
        is_banned=False,
        platform_role=platform_role,
    )


@pytest.fixture(autouse=True)
def _reset_throttle():
    reveal_throttle._reset_for_tests()
    yield
    reveal_throttle._reset_for_tests()


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
async def personal_cred(adb):
    """Personal-cred владельца OWNER_USER_ID в OWNER_DEPT с реальным секретом."""
    cred = await cred_repo.create(
        adb,
        id="cred_pers_uacl01",
        name="personal_jira",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_USER_ID,
        owner_user_dept_id=OWNER_DEPT,
        owner_dept_id=None,
        login="alice",
        secret_encrypted=secrets_service.encrypt(
            "topsecret",
            aad=secrets_service.aad_for_credential("cred_pers_uacl01"),
        ),
        status="active",
        created_by=OWNER_USER_ID,
    )
    await adb.commit()
    return cred


# ── ADD ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_grants_user_acl_ok(http_client, personal_cred):
    payload = {"user_id": GRANTEE_ID, "can_read": True, "can_write": False}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl", json=payload
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user_id"] == GRANTEE_ID
    assert body["can_read"] is True
    assert body["can_write"] is False
    assert body["granted_by_user_id"] == OWNER_USER_ID
    assert body["id"].startswith("uacl_")


@pytest.mark.asyncio
async def test_grant_to_owner_rejected(http_client, personal_cred):
    payload = {"user_id": OWNER_USER_ID, "can_read": True}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl", json=payload
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] in (
        "USER_ACL_OWNER_REDUNDANT",
        "USER_ACL_SELF_REDUNDANT",
    )


@pytest.mark.asyncio
async def test_duplicate_grant_409(http_client, personal_cred):
    payload = {"user_id": GRANTEE_ID, "can_read": True}
    r1 = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl", json=payload
    )
    assert r1.status_code == 201
    r2 = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl", json=payload
    )
    assert r2.status_code == 409
    assert r2.json()["error_code"] == "USER_ACL_DUPLICATE"


@pytest.mark.asyncio
async def test_non_owner_cannot_grant(http_client, personal_cred):
    # Чужой пользователь не видит personal-креду → 404 (info-leak).
    _set_identity(_identity(user_id=OUTSIDER_ID, department_id=GRANTEE_DEPT))
    payload = {"user_id": GRANTEE_ID, "can_read": True}
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl", json=payload
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_grant_cred_not_found(http_client):
    payload = {"user_id": GRANTEE_ID, "can_read": True}
    resp = await http_client.post(
        "/api/secret/v1/credentials/cred_missing/user-acl", json=payload
    )
    assert resp.status_code == 404


# ── LIST ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_user_acls(http_client, personal_cred, adb):
    await uacl_repo.create(
        adb,
        id="uacl_lst1",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    resp = await http_client.get(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl"
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["user_id"] == GRANTEE_ID


# ── REVOKE ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_user_acl_ok(http_client, personal_cred, adb):
    acl = await uacl_repo.create(
        adb,
        id="uacl_rv1",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl/{acl.id}"
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_revoke_user_acl_not_found(http_client, personal_cred):
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl/uacl_missing"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_revoke(http_client, personal_cred, adb):
    acl = await uacl_repo.create(
        adb,
        id="uacl_rv2",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    _set_identity(_identity(user_id=OUTSIDER_ID, department_id=GRANTEE_DEPT))
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{personal_cred.id}/user-acl/{acl.id}"
    )
    assert resp.status_code in (403, 404)


# ── ACCESS: grantee reveal / non-grantee denied ─────────────────────────────


@pytest.mark.asyncio
async def test_grantee_can_reveal(http_client, personal_cred, adb):
    await uacl_repo.create(
        adb,
        id="uacl_read1",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    # Grantee из своего (другого) департамента — нет роли на креду, только user-ACL.
    _set_identity(_identity(
        user_id=GRANTEE_ID,
        department_id=GRANTEE_DEPT,
        roles=["reader"],
    ))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/reveal"
    )
    assert resp.status_code == 200, resp.text
    decoded = base64.b64decode(resp.json()["secret_b64"]).decode("utf-8")
    assert decoded == "topsecret"


@pytest.mark.asyncio
async def test_grantee_can_read_metadata(http_client, personal_cred, adb):
    await uacl_repo.create(
        adb,
        id="uacl_read2",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    _set_identity(_identity(
        user_id=GRANTEE_ID, department_id=GRANTEE_DEPT, roles=["reader"]
    ))
    resp = await http_client.get(
        f"/api/secret/v1/credentials/{personal_cred.id}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == personal_cred.id


@pytest.mark.asyncio
async def test_non_grantee_reveal_denied(http_client, personal_cred):
    # Outsider без user-ACL → personal-cred скрыта (404 info-leak).
    _set_identity(_identity(
        user_id=OUTSIDER_ID, department_id=GRANTEE_DEPT, roles=["reader"]
    ))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{personal_cred.id}/reveal"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_read_only_grant_cannot_write(http_client, personal_cred, adb):
    await uacl_repo.create(
        adb,
        id="uacl_ro1",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    _set_identity(_identity(
        user_id=GRANTEE_ID, department_id=GRANTEE_DEPT, roles=["operator"]
    ))
    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{personal_cred.id}",
        json={"login": "newlogin"},
    )
    # can_read без can_write — write-ветка не пускает; personal info-leak → 404.
    assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_write_grant_can_update(http_client, personal_cred, adb):
    await uacl_repo.create(
        adb,
        id="uacl_rw1",
        cred_id=personal_cred.id,
        user_id=GRANTEE_ID,
        can_read=True,
        can_write=True,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()
    _set_identity(_identity(
        user_id=GRANTEE_ID, department_id=GRANTEE_DEPT, roles=["operator"]
    ))
    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{personal_cred.id}",
        json={"login": "newlogin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["login"] == "newlogin"
