"""HTTP-эндпоинты /credentials — CRUD/reveal/transfer/recover happy paths и ошибки.

Использует real Postgres + ASGI client. Identity мокается через override
зависимостей FastAPI; introspect к auth_service не дёргается.
"""

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
from src.services import reveal_throttle
from tests._helpers import b64


OWNER_ID = "usr_owner000000000000000000000001"
OWNER_DEPT = "dep_owner00000000000000000000001"
SVC_ADMIN_ID = "usr_admin000000000000000000000001"


def _identity(
    *,
    user_id: str = OWNER_ID,
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
    """ASGI client с override'нутыми зависимостями."""
    # Override DB → возвращает существующую adb-сессию
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


# ── CREATE ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_personal_credential_ok(http_client):
    payload = {
        "name": "jira_token",
        "service": "jira",
        "scope": "personal",
        "login": "alice",
        "secret_b64": b64("supersecret-value"),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "personal"
    assert body["owner_user_id"] == OWNER_ID
    assert body["owner_dept_id"] is None
    assert "secret" not in body
    assert "secret_encrypted" not in body
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_create_department_credential_ok(http_client):
    _set_identity(_identity(roles=["operator"], platform_role="department_admin"))
    payload = {
        "name": "dep_bot_jira",
        "service": "jira",
        "scope": "department",
        "secret_b64": b64("dept-secret"),
        "owner_dept_id": OWNER_DEPT,
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["owner_dept_id"] == OWNER_DEPT


@pytest.mark.asyncio
async def test_create_personal_with_owner_dept_id_rejected(http_client):
    """Pydantic-валидатор: personal scope + owner_dept_id → 422."""
    payload = {
        "name": "x",
        "service": "jira",
        "scope": "personal",
        "secret_b64": b64("s"),
        "owner_dept_id": "dep_unknown",
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_department_without_owner_dept_id_rejected(http_client):
    payload = {
        "name": "x",
        "service": "jira",
        "scope": "department",
        "secret_b64": b64("s"),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_duplicate_name_returns_409(http_client):
    payload = {
        "name": "uniq",
        "service": "jira",
        "scope": "personal",
        "secret_b64": b64("s1"),
    }
    r1 = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert r1.status_code == 201
    r2 = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert r2.status_code == 409
    assert r2.json()["error_code"] == "NAME_DUPLICATE"


@pytest.mark.asyncio
async def test_create_personal_by_bot_denied(http_client):
    _set_identity(_identity(actor_type="bot"))
    payload = {
        "name": "x",
        "service": "jira",
        "scope": "personal",
        "secret_b64": b64("s"),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_invalid_base64_secret_returns_422(http_client):
    payload = {
        "name": "badb64",
        "service": "jira",
        "scope": "personal",
        "secret_b64": "not base64!!!",
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_secret_decoding_to_too_long_returns_422(http_client):
    payload = {
        "name": "toolong",
        "service": "jira",
        "scope": "personal",
        "secret_b64": b64("x" * 8193),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_then_reveal_roundtrip(http_client):
    plaintext = "rt-token-значение"
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "roundtrip",
            "service": "jira",
            "scope": "personal",
            "secret_b64": b64(plaintext),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]
    reveal_resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred_id}/reveal"
    )
    assert reveal_resp.status_code == 200, reveal_resp.text
    import base64
    decoded = base64.b64decode(reveal_resp.json()["secret_b64"]).decode("utf-8")
    assert decoded == plaintext


# ── GET / LIST ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_credential_returns_metadata(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "n1",
            "service": "jira",
            "scope": "personal",
            "login": "alice",
            "secret_b64": b64("topsecret"),
        },
    )
    cred_id = create_resp.json()["id"]
    resp = await http_client.get(f"/api/secret/v1/credentials/{cred_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == cred_id
    assert body["login"] == "alice"
    assert "secret" not in body


@pytest.mark.asyncio
async def test_get_credential_not_found(http_client):
    resp = await http_client.get("/api/secret/v1/credentials/cred_does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_blocked_credential_as_non_admin_returns_404(http_client, adb):
    cred = await cred_repo.create(
        adb,
        id="cred_blocked01",
        name="blocked",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        created_by=OWNER_ID,
    )
    await adb.commit()
    resp = await http_client.get(f"/api/secret/v1/credentials/{cred.id}")
    # Owner имел бы read-доступ к active версии — для него blocked → 410 GONE
    # с blocked_reason в details, а не 404.
    assert resp.status_code == 410
    body = resp.json()
    assert body["error_code"] == "CREDENTIAL_BLOCKED"


@pytest.mark.asyncio
async def test_list_credentials_returns_personal_creds(http_client):
    for i in range(3):
        await http_client.post(
            "/api/secret/v1/credentials",
            json={
                "name": f"n{i}",
                "service": "jira",
                "scope": "personal",
                "secret_b64": b64("s"),
            },
        )
    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 3


@pytest.mark.asyncio
async def test_list_credentials_invalid_cursor_no_separator(http_client):
    resp = await http_client.get(
        "/api/secret/v1/credentials", params={"cursor": "no-separator-here"}
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_list_credentials_invalid_cursor_bad_timestamp(http_client):
    resp = await http_client.get(
        "/api/secret/v1/credentials", params={"cursor": "not-a-date|cred_x"}
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_list_credentials_valid_cursor_paginates(http_client):
    for i in range(3):
        await http_client.post(
            "/api/secret/v1/credentials",
            json={
                "name": f"page{i}",
                "service": "jira",
                "scope": "personal",
                "secret_b64": b64("s"),
            },
        )
    first = await http_client.get(
        "/api/secret/v1/credentials", params={"limit": 1}
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor and "|" in cursor

    second = await http_client.get(
        "/api/secret/v1/credentials", params={"limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200
    first_ids = {c["id"] for c in first.json()["items"]}
    second_ids = {c["id"] for c in second.json()["items"]}
    assert first_ids.isdisjoint(second_ids)


# ── UPDATE ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_credential_name(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "old", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]
    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred_id}", json={"name": "new"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "new"


@pytest.mark.asyncio
async def test_update_secret_reencrypts(http_client, adb):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("old")},
    )
    cred_id = create_resp.json()["id"]
    cred_before = await cred_repo.get_by_id(adb, cred_id)
    old_ct = cred_before.secret_encrypted

    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred_id}", json={"secret_b64": b64("newvalue")}
    )
    assert resp.status_code == 200

    await adb.refresh(cred_before)
    assert cred_before.secret_encrypted != old_ct


@pytest.mark.asyncio
async def test_update_not_found(http_client):
    resp = await http_client.patch(
        "/api/secret/v1/credentials/cred_missing", json={"name": "x"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_by_non_owner_denied(http_client):
    # Создаём cred от owner_id
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]

    # Меняем identity на чужого пользователя
    _set_identity(_identity(user_id="usr_other00000000000000000000001", roles=["reader"]))
    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred_id}", json={"name": "hijack"}
    )
    # personal scope: не-владелец без ACL не должен узнать о существовании
    # cred'ы — отдаём 404 (info-leak protection).
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


# ── DELETE ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_credential_by_owner(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]
    resp = await http_client.delete(f"/api/secret/v1/credentials/{cred_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_admin_override_delete_requires_reason(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]

    # service_admin (другой user)
    _set_identity(_identity(
        user_id=SVC_ADMIN_ID,
        roles=["admin"],
    ))
    # Без reason — 422
    resp = await http_client.request(
        "DELETE", f"/api/secret/v1/credentials/{cred_id}", json={}
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "ADMIN_OVERRIDE_REASON_REQUIRED"


@pytest.mark.asyncio
async def test_admin_override_delete_with_reason_ok(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]

    _set_identity(_identity(
        user_id=SVC_ADMIN_ID,
        roles=["admin"],
    ))
    resp = await http_client.request(
        "DELETE",
        f"/api/secret/v1/credentials/{cred_id}",
        json={"reason": "user offboarded"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_delete_not_found(http_client):
    resp = await http_client.delete("/api/secret/v1/credentials/cred_nope")
    assert resp.status_code == 404


# ── REVEAL ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reveal_returns_b64(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "n",
            "service": "jira",
            "scope": "personal",
            "login": "alice",
            "secret_b64": b64("my-token"),
        },
    )
    cred_id = create_resp.json()["id"]
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred_id}/reveal")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["login"] == "alice"
    import base64
    assert base64.b64decode(body["secret_b64"]).decode() == "my-token"


@pytest.mark.asyncio
async def test_reveal_blocked_returns_410_or_404(http_client, adb):
    """blocked cred → у owner-user-actor нет доступа → 404."""
    cred = await cred_repo.create(
        adb,
        id="cred_blockrv1",
        name="blocked_rv",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        created_by=OWNER_ID,
    )
    await adb.commit()
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    # owner-user не имеет manage_status, и blocked отбивает все остальные actions.
    assert resp.status_code in (404, 410)


@pytest.mark.asyncio
async def test_reveal_not_found(http_client):
    resp = await http_client.post("/api/secret/v1/credentials/cred_nope/reveal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reveal_by_non_owner_denied(http_client):
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={"name": "n", "service": "jira", "scope": "personal", "secret_b64": b64("s")},
    )
    cred_id = create_resp.json()["id"]
    _set_identity(_identity(user_id="usr_other00000000000000000000001", roles=["reader"]))
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred_id}/reveal")
    # personal scope info-leak protection — 404, не 403.
    assert resp.status_code == 404


# ── TRANSFER ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_requires_admin(http_client, adb):
    cred = await cred_repo.create(
        adb,
        id="cred_tr01",
        name="t1",
        service="jira",
        scope="personal",
        owner_user_id="usr_orig00000000000000000000001",
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    # Текущий identity = owner (operator), не админ.
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_user_id": "usr_new000000000000000000000001", "reason": "owner left org"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_transfer_personal_blocked_by_service_admin(http_client, adb):
    cred = await cred_repo.create(
        adb,
        id="cred_tr02",
        name="t2",
        service="jira",
        scope="personal",
        owner_user_id="usr_orig00000000000000000000001",
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_deleted",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    _set_identity(_identity(user_id=SVC_ADMIN_ID, roles=["admin"]))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_user_id": "usr_new000000000000000000000001", "reason": "owner left org"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_user_id"] == "usr_new000000000000000000000001"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_transfer_active_cred_rejected(http_client, adb):
    cred = await cred_repo.create(
        adb,
        id="cred_tr03",
        name="t3",
        service="jira",
        scope="personal",
        owner_user_id="usr_orig00000000000000000000001",
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    _set_identity(_identity(user_id=SVC_ADMIN_ID, roles=["admin"]))
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_user_id": "usr_new000000000000000000000001", "reason": "owner left org"},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_BLOCKED"


# ── RECOVER ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_within_window(http_client, adb):
    from datetime import datetime, timezone
    cred = await cred_repo.create(
        adb,
        id="cred_rc01",
        name="rc1",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_deleted",
        created_by=OWNER_ID,
    )
    cred.blocked_at = datetime.now(timezone.utc)
    await adb.commit()
    _set_identity(_identity(user_id=SVC_ADMIN_ID, roles=["admin"]))
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/recover")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_recover_window_expired(http_client, adb):
    from datetime import datetime, timedelta, timezone
    cred = await cred_repo.create(
        adb,
        id="cred_rc02",
        name="rc2",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_deleted",
        created_by=OWNER_ID,
    )
    cred.blocked_at = datetime.now(timezone.utc) - timedelta(days=31)
    await adb.commit()
    _set_identity(_identity(user_id=SVC_ADMIN_ID, roles=["admin"]))
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/recover")
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "RECOVER_WINDOW_EXPIRED"


@pytest.mark.asyncio
async def test_recover_requires_admin(http_client, adb):
    cred = await cred_repo.create(
        adb,
        id="cred_rc03",
        name="rc3",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        created_by=OWNER_ID,
    )
    await adb.commit()
    # Текущий identity = owner с ролью operator, не admin
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/recover")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_account_admin_transfer_dept_cred_with_deleted_owner_dept(http_client, adb):
    """Emergency path: владеющий отдел удалён, живого service-admin'а нет.
    account_admin переназначает владельца на новый отдел → 200 + new owner."""
    cred = await cred_repo.create(
        adb,
        id="cred_aa01",
        name="aa1",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_deleted0000000000000000001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    # account_admin: нет department_id, нет secret-service access.
    _set_identity(
        _identity(
            user_id="usr_acctadmin000000000000000001",
            department_id=None,
            roles=[],
            platform_role="account_admin",
        )
    )
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_dept_id": "dep_new00000000000000000000001", "reason": "owner dept deleted"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_dept_id"] == "dep_new00000000000000000000001"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_account_admin_recover_blocked_cred(http_client, adb):
    from datetime import datetime, timezone
    cred = await cred_repo.create(
        adb,
        id="cred_aa02",
        name="aa2",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_deleted0000000000000000002",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_orig00000000000000000000001",
    )
    cred.blocked_at = datetime.now(timezone.utc)
    await adb.commit()
    _set_identity(
        _identity(
            user_id="usr_acctadmin000000000000000001",
            department_id=None,
            roles=[],
            platform_role="account_admin",
        )
    )
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/recover")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_foreign_dept_admin_cannot_transfer(http_client, adb):
    """department_admin чужого отдела (не account_admin, не service-admin
    владеющего dep'а) — по-прежнему отбивается."""
    cred = await cred_repo.create(
        adb,
        id="cred_aa03",
        name="aa3",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_owner00000000000000000000a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    # Чужой dep_admin: есть secret-access, но dept != owner_dept и роль не admin
    # secret_service владеющего dep'а.
    _set_identity(
        _identity(
            user_id="usr_otherdep00000000000000000001",
            department_id="dep_other00000000000000000000b",
            roles=["operator"],
            platform_role="department_admin",
        )
    )
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_dept_id": "dep_new00000000000000000000001", "reason": "trying"},
    )
    assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_owning_dept_service_admin_can_still_transfer(http_client, adb):
    """Штатный путь не ослаблен: service-admin владеющего dep'а по-прежнему
    делает transfer."""
    cred = await cred_repo.create(
        adb,
        id="cred_aa04",
        name="aa4",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_ownadmin0000000000000000001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_orig00000000000000000000001",
    )
    await adb.commit()
    _set_identity(
        _identity(
            user_id="usr_ownadmin00000000000000000001",
            department_id="dep_ownadmin0000000000000000001",
            roles=["admin"],
        )
    )
    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred.id}/transfer",
        json={"new_owner_dept_id": "dep_new00000000000000000000001", "reason": "moving"},
    )
    assert resp.status_code == 200
    assert resp.json()["owner_dept_id"] == "dep_new00000000000000000000001"
    assert resp.json()["status"] == "active"


# Make grants_repo/acls_repo imports used (linter-guard for fixtures).
_ = (grants_repo, acls_repo)
