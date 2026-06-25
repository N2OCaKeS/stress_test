"""Guest-visibility: видимость кред'ов для роли `guest`.

Контракт:
* guest видит в списке только cred'ы своего dep'а с `visible_to_dept=True`;
* shape ответа — `CredentialGuestList` (без owner_user_id/login/created_by/
  timestamps/blocked-полей);
* любой не-list action (read/reveal/write/delete) — запрещён, даже на
  visible_to_dept-cred своего dep'а;
* для access_service.list_guest проверка short-circuit: visible_to_dept +
  owner_dept_id совпадает → True; иначе False.
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
    visible_to_dept: bool,
    name: str = "shared",
):
    return await cred_repo.create(
        adb,
        id=id,
        name=name,
        service="jira",
        scope="department",
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
async def test_guest_sees_only_visible_to_dept_in_own_dept(http_client, adb):
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
    assert names == {"own_visible"}
    # Shape: только разрешённые поля.
    item = body["items"][0]
    assert set(item.keys()) == {"id", "name", "service", "scope", "visible_to_dept"}
    assert item["id"] == own_visible.id
    assert item["visible_to_dept"] is True


@pytest.mark.asyncio
async def test_guest_does_not_see_visible_in_foreign_dept(http_client, adb):
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
    body = resp.json()
    assert body["items"] == []


@pytest.mark.asyncio
async def test_guest_list_response_has_no_metadata_leak(http_client, adb):
    await _create_dept_cred(
        adb,
        id="cred_meta_test01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="metatest",
    )
    await adb.commit()

    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200
    body = resp.json()
    leaked_keys = {
        "owner_user_id", "owner_dept_id", "login", "status",
        "created_by", "created_at", "updated_at",
        "blocked_at", "blocked_reason",
        "secret", "secret_encrypted", "secret_b64",
    }
    for item in body["items"]:
        assert leaked_keys.isdisjoint(item.keys()), (
            f"guest response leaked keys: {set(item.keys()) & leaked_keys}"
        )


# ── non-list actions denied ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guest_cannot_get_visible_cred(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_get_guest01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="for_get",
    )
    await adb.commit()

    resp = await http_client.get(f"/api/secret/v1/credentials/{cred.id}")
    # guest_role_no_access теперь включён в _NOT_VISIBLE_REASONS — прямой GET
    # известного cred_id отдаёт 404 (info-leak protection), а не 403, чтобы
    # guest не мог перебирать ID и обнаруживать существование чужих кред с
    # `visible_to_dept=False`.
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_guest_cannot_reveal(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_reveal_guest01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="for_reveal",
    )
    await adb.commit()

    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    # Тот же info-leak protection — reveal по известному id для guest → 404.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_guest_cannot_update(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_update_guest01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="for_update",
    )
    await adb.commit()

    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred.id}",
        json={"name": "renamed"},
    )
    # update проходит через load_for_action → info-leak 404 для guest.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_guest_cannot_delete(http_client, adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_delete_guest01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
        name="for_delete",
    )
    await adb.commit()

    resp = await http_client.delete(f"/api/secret/v1/credentials/{cred.id}")
    # Guest на любой прямой операции с кред'ой получает 404, как и на GET:
    # роль guest живёт только через list_guest, существование кред'ы через
    # delete-отказ ей не раскрываем.
    assert resp.status_code == 404


# ── access_service.check_access(list_guest) ─────────────────────────────────


@pytest.mark.asyncio
async def test_access_service_list_guest_allows_visible_in_own_dept(adb):
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
async def test_access_service_list_guest_denies_invisible_in_own_dept(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_hidden01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=False,
    )
    actor = _identity()
    allowed, reason = await access_service.check_access(adb, actor, cred, "list_guest")
    assert allowed is False
    assert reason == "guest_not_visible"


@pytest.mark.asyncio
async def test_access_service_list_guest_denies_visible_in_foreign_dept(adb):
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
async def test_access_service_other_actions_denied_for_guest(adb):
    cred = await _create_dept_cred(
        adb,
        id="cred_acc_guest_other01",
        owner_dept_id=GUEST_DEPT,
        visible_to_dept=True,
    )
    actor = _identity()
    for action in ("read", "reveal", "write", "delete", "manage_status"):
        allowed, reason = await access_service.check_access(adb, actor, cred, action)
        assert allowed is False, f"guest must NOT be allowed for action={action}"
        assert reason == "guest_role_no_access"
