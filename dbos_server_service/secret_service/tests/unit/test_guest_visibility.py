"""Guest-visibility: видимость кред'ов для системной роли `guest`.

Контракт (уровень `view` на весь отдел):
* guest видит в списке метаданные ВСЕХ department/cross_department-кред своего
  dep'а — флаг `visible_to_dept` больше не фильтрует;
* shape ответа — `CredentialGuestList`: id/name/service/scope/login/
  visible_to_dept, без owner_*/created_by/timestamps/blocked-полей и значения;
* GET карточки своей dep-cred'ы → 200 с той же урезанной проекцией;
* reveal/write/delete на своей dep-cred'е → 403 (видит, но не значение/не
  правит); на чужой dep / personal — 404-маска;
* для access_service: read/list_guest на dep-cred'е своего отдела → True;
  value-actions → отказ.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app
from src.repositories import credentials as cred_repo
from src.services import access_service, reveal_throttle


GUEST_DEPT = "dep_guest00000000000000000000001"
OTHER_DEPT = "dep_other00000000000000000000001"
GUEST_USER = "usr_guest00000000000000000000001"
SVC = "secret_service"


def _envelope() -> str:
    return "v2$nonce$ct"


def _identity(
    *,
    roles: list[str] | None = None,
    user_id: str = GUEST_USER,
    department_id: str | None = GUEST_DEPT,
    actor_type: str = "user",
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="guest_actor",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=[SVC],
        service_roles={SVC: roles if roles is not None else ["guest"]},
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


async def _create_dept_cred(
    adb,
    *,
    id: str,
    owner_dept_id: str,
    visible_to_dept: bool = True,
    scope: str = "department",
    name: str = "shared",
):
    return await cred_repo.create(
        adb,
        id=id,
        name=name,
        service="jira",
        scope=scope,
        owner_user_id=None,
        owner_dept_id=owner_dept_id,
        login="x",
        secret_encrypted=_envelope(),
        status="active",
        created_by="usr_admin00000000000000000000001",
        visible_to_dept=visible_to_dept,
    )


# ── list endpoint ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guest_sees_all_own_dept_creds(http_client, adb):
    own_visible = await _create_dept_cred(
        adb,
        id="cred_dept_visible01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="own_visible",
    )
    await _create_dept_cred(
        adb,
        id="cred_dept_hidden01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=False,
        name="own_hidden",
    )
    await adb.commit()

    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {item["name"] for item in body["items"]}
    # visible_to_dept больше не фильтрует — guest видит обе cred'ы отдела.
    assert names == {"own_visible", "own_hidden"}
    item = next(i for i in body["items"] if i["id"] == own_visible.id)
    assert set(item.keys()) == {
        "id", "name", "service", "scope", "login", "visible_to_dept"
    }


@pytest.mark.asyncio
async def test_guest_does_not_see_foreign_dept(http_client, adb):
    await _create_dept_cred(
        adb,
        id="cred_other_visible01",
        owner_dept_id=OTHER_DEPT,
        visible_to_dept=True,
        name="other_visible",
    )
    await adb.commit()

    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_guest_list_response_has_no_value_or_owner_leak(http_client, adb):
    await _create_dept_cred(
        adb,
        id="cred_meta_test01",
        owner_dept_id=GUEST_DEPT,
        name="metatest",
    )
    await adb.commit()

    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200
    body = resp.json()
    # login теперь часть guest-проекции (метаданные), значение и служебные
    # поля — по-прежнему нет.
    leaked_keys = {
        "owner_user_id", "owner_dept_id", "status",
        "created_by", "created_at", "updated_at",
        "blocked_at", "blocked_reason",
        "secret", "secret_encrypted", "secret_b64",
    }
    for item in body["items"]:
        assert leaked_keys.isdisjoint(item.keys()), (
            f"guest response leaked keys: {set(item.keys()) & leaked_keys}"
        )


@pytest.mark.asyncio
async def test_guest_without_department_sees_nothing(http_client, adb):
    await _create_dept_cred(
        adb,
        id="cred_no_dept_guest1",
        owner_dept_id=GUEST_DEPT,
        name="orphan_visible",
    )
    await adb.commit()

    _set_identity(_identity(department_id=None))
    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


# ── single-card GET ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guest_get_own_dept_cred_returns_metadata(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_get_guest01",
        owner_dept_id=GUEST_DEPT,
        name="for_get",
    )
    await adb.commit()

    resp = await http_client.get(f"/api/secret/v1/credentials/{cred.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "service", "scope", "login", "visible_to_dept"
    }
    assert body["id"] == cred.id
    assert body["login"] == "x"


@pytest.mark.asyncio
async def test_guest_get_foreign_dept_cred_404(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_get_foreign01",
        owner_dept_id=OTHER_DEPT,
        name="foreign_get",
    )
    await adb.commit()

    resp = await http_client.get(f"/api/secret/v1/credentials/{cred.id}")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


# ── value/write actions denied ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guest_cannot_reveal_own_dept_cred(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_reveal_guest01",
        owner_dept_id=GUEST_DEPT,
        name="for_reveal",
    )
    await adb.commit()

    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    # Видит метаданные, но не значение → честный 403.
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guest_reveal_foreign_dept_404(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_reveal_foreign01",
        owner_dept_id=OTHER_DEPT,
        name="for_reveal_foreign",
    )
    await adb.commit()

    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_guest_cannot_update_own_dept_cred(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_update_guest01",
        owner_dept_id=GUEST_DEPT,
        name="for_update",
    )
    await adb.commit()

    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred.id}",
        json={"name": "renamed"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guest_cannot_delete_own_dept_cred(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_delete_guest01",
        owner_dept_id=GUEST_DEPT,
        name="for_delete",
    )
    await adb.commit()

    resp = await http_client.delete(f"/api/secret/v1/credentials/{cred.id}")
    assert resp.status_code == 403


# ── access_service.check_access ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_access_service_list_guest_allows_own_dept(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_ok01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
    )
    actor = _identity()
    allowed, reason = await access_service.check_access(adb, actor, cred, "list_guest")
    assert allowed is True
    assert reason == "guest_visible_to_dept"


@pytest.mark.asyncio
async def test_access_service_list_guest_allows_own_dept_even_if_hidden(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_hidden01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=False,
    )
    actor = _identity()
    allowed, reason = await access_service.check_access(adb, actor, cred, "list_guest")
    assert allowed is True
    assert reason == "guest_visible_to_dept"


@pytest.mark.asyncio
async def test_access_service_list_guest_denies_foreign_dept(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_foreign01",
        owner_dept_id=OTHER_DEPT,
        visible_to_dept=True,
    )
    actor = _identity()
    allowed, reason = await access_service.check_access(adb, actor, cred, "list_guest")
    assert allowed is False
    assert reason == "guest_not_visible"


@pytest.mark.asyncio
async def test_access_service_guest_read_allowed_own_dept(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_read01",
        owner_dept_id=GUEST_DEPT,
    )
    actor = _identity()
    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert allowed is True
    assert reason == "guest_visible_to_dept"


@pytest.mark.asyncio
async def test_access_service_guest_value_actions_denied(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_other01",
        owner_dept_id=GUEST_DEPT,
    )
    actor = _identity()
    for action in ("reveal", "write", "delete", "manage_status", "grant_acl"):
        allowed, reason = await access_service.check_access(adb, actor, cred, action)
        assert allowed is False, f"guest must NOT be allowed for action={action}"
        assert reason == "guest_no_value_access"
