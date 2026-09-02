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
THIRD_DEPT = "dep_third00000000000000000000001"


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


# ── UPSERT (PUT) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_acl_creates(http_client, dept_cred, adb):
    payload = {
        "dept_id": OWNER_DEPT,
        "role_name": "reader",
        "can_read": True,
        "can_write": False,
    }
    resp = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl", json=payload
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["acl"]["can_read"] is True
    assert body["acl"]["can_write"] is False
    assert body["acl"]["role_name"] == "reader"

    rows = await acls_repo.get_for_cred(adb, dept_cred.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_upsert_acl_updates_flags_no_409(http_client, dept_cred, adb):
    base = {"dept_id": OWNER_DEPT, "role_name": "operator"}
    r1 = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={**base, "can_read": True, "can_write": False},
    )
    assert r1.status_code == 200
    first_id = r1.json()["acl"]["id"]

    # Повторный PUT той же пары не 409'ит, а переписывает флаги поверх строки.
    r2 = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={**base, "can_read": True, "can_write": True},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["acl"]["id"] == first_id
    assert r2.json()["acl"]["can_write"] is True

    rows = await acls_repo.get_for_cred(adb, dept_cred.id)
    assert len(rows) == 1
    assert rows[0].can_write is True


@pytest.mark.asyncio
async def test_add_acl_view_only_roundtrips(http_client, dept_cred):
    payload = {
        "dept_id": OWNER_DEPT,
        "role_name": "guest",
        "can_view": True,
        "can_read": False,
        "can_write": False,
    }
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl", json=payload
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["can_view"] is True
    assert body["can_read"] is False
    assert body["can_write"] is False


@pytest.mark.asyncio
async def test_upsert_view_only_persists_not_removed(http_client, dept_cred, adb):
    # view-only (можно видеть метаданные) — валидная выдача, строку не сносим.
    resp = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={
            "dept_id": OWNER_DEPT,
            "role_name": "guest",
            "can_view": True,
            "can_read": False,
            "can_write": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["acl"] is not None
    assert resp.json()["acl"]["can_view"] is True

    rows = await acls_repo.get_for_cred(adb, dept_cred.id)
    assert len(rows) == 1
    assert rows[0].can_view is True


@pytest.mark.asyncio
async def test_upsert_write_normalizes_lower_levels(http_client, dept_cred, adb):
    # Выдаём только can_write — лесенка должна подтянуть can_read и can_view.
    resp = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={
            "dept_id": OWNER_DEPT,
            "role_name": "operator",
            "can_view": False,
            "can_read": False,
            "can_write": True,
        },
    )
    assert resp.status_code == 200, resp.text
    acl = resp.json()["acl"]
    assert acl["can_write"] is True
    assert acl["can_read"] is True
    assert acl["can_view"] is True


@pytest.mark.asyncio
async def test_upsert_acl_both_false_removes_row(http_client, dept_cred, adb):
    base = {"dept_id": OWNER_DEPT, "role_name": "reader"}
    r1 = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={**base, "can_read": True, "can_write": False},
    )
    assert r1.status_code == 200

    # Снимаем оба флага — строка должна исчезнуть, ответ acl=null.
    r2 = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={**base, "can_read": False, "can_write": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True
    assert r2.json()["acl"] is None

    rows = await acls_repo.get_for_cred(adb, dept_cred.id)
    assert rows == []


@pytest.mark.asyncio
async def test_upsert_acl_both_false_idempotent_noop(http_client, dept_cred, adb):
    # Снять несуществующую строку — идемпотентный no-op, без ошибки.
    resp = await http_client.put(
        f"/api/secret/v1/credentials/{dept_cred.id}/acl",
        json={
            "dept_id": OWNER_DEPT,
            "role_name": "guest",
            "can_read": False,
            "can_write": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["acl"] is None
    rows = await acls_repo.get_for_cred(adb, dept_cred.id)
    assert rows == []


@pytest.mark.asyncio
async def test_upsert_acl_cross_dep_without_grant_rejected(
    http_client, cross_dep_cred
):
    _set_identity(_identity(
        department_id=RECIPIENT_DEPT,
        platform_role="department_admin",
    ))
    resp = await http_client.put(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl",
        json={
            "dept_id": RECIPIENT_DEPT,
            "role_name": "reader",
            "can_read": True,
            "can_write": False,
        },
    )
    assert resp.status_code in (404, 422)


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


# ── cross-recipient isolation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recipient_dep_admin_cannot_grant_for_foreign_dept(
    http_client, cross_dep_cred, adb
):
    # Оба recipient-отдела имеют DeptGrant на кред'у.
    await grants_repo.create(
        adb,
        id="dgr_iso_x",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=RECIPIENT_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await grants_repo.create(
        adb,
        id="dgr_iso_y",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=THIRD_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()

    # dep_admin отдела X (RECIPIENT_DEPT) пытается выдать RoleACL отделу Y.
    _set_identity(_identity(
        department_id=RECIPIENT_DEPT,
        platform_role="department_admin",
    ))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl",
        json={
            "dept_id": THIRD_DEPT,
            "role_name": "reader",
            "can_read": True,
            "can_write": True,
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_ACL_FOREIGN_DEPT"

    # Строка ACL для Y не создалась.
    rows = await acls_repo.get_for_cred(adb, cross_dep_cred.id)
    assert all(r.dept_id != THIRD_DEPT for r in rows)


@pytest.mark.asyncio
async def test_recipient_dep_admin_cannot_revoke_foreign_dept_acl(
    http_client, cross_dep_cred, adb
):
    await grants_repo.create(
        adb,
        id="dgr_iso_rx",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=RECIPIENT_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await grants_repo.create(
        adb,
        id="dgr_iso_ry",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=THIRD_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    # ACL отдела Y (owner-side его завёл).
    acl_y = await acls_repo.create(
        adb,
        id="acl_iso_y",
        cred_id=cross_dep_cred.id,
        dept_id=THIRD_DEPT,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()

    # dep_admin отдела X пытается снести ACL отдела Y.
    _set_identity(_identity(
        department_id=RECIPIENT_DEPT,
        platform_role="department_admin",
    ))
    resp = await http_client.delete(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl/{acl_y.id}"
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_ACL_FOREIGN_DEPT"

    # ACL отдела Y на месте.
    rows = await acls_repo.get_for_cred(adb, cross_dep_cred.id)
    assert any(r.id == acl_y.id for r in rows)


@pytest.mark.asyncio
async def test_owner_dep_admin_can_manage_any_recipient_acl(
    http_client, cross_dep_cred, adb
):
    # Owner-side dep_admin по-прежнему адресует любой recipient с DeptGrant.
    await grants_repo.create(
        adb,
        id="dgr_iso_owner_y",
        cred_id=cross_dep_cred.id,
        recipient_dept_id=THIRD_DEPT,
        granted_by_user_id=OWNER_USER_ID,
    )
    await adb.commit()

    # http_client по умолчанию — owner dep_admin (OWNER_DEPT).
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cross_dep_cred.id}/acl",
        json={
            "dept_id": THIRD_DEPT,
            "role_name": "reader",
            "can_read": True,
            "can_write": False,
        },
    )
    assert resp.status_code == 201, resp.text


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
