"""Окно валидности кред'ы (`valid_from` / `valid_to`).

Контракт:
* `valid_from` в будущем → reveal до того отдаёт `410 SECRET_NOT_YET_VALID`;
* `valid_to` в прошлом → reveal отдаёт `410 SECRET_EXPIRED`;
* `valid_from > valid_to` на create → `422 VALIDATION_ERROR`;
* `valid_to < now` на create → `422 VALIDATION_ERROR` (нельзя создавать уже истёкший);
* metadata-GET остаётся `200` независимо от срока;
* PATCH `valid_to` (продление) → reveal снова `200`;
* guest-листинг НЕ показывает `valid_from`/`valid_to`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app
from src.repositories import credentials as cred_repo
from src.services import reveal_throttle


OWNER_ID = "usr_valid000000000000000000000001"
OWNER_DEPT = "dep_valid000000000000000000000001"


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


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── Create ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_valid_from_in_future_returns_201(http_client):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    payload = {
        "name": "future_token",
        "service": "jira",
        "scope": "personal",
        "login": "alice",
        "secret": "supersecret",
        "valid_from": _iso(future),
        "valid_to": _iso(far_future),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["valid_from"] is not None
    assert body["valid_to"] is not None


@pytest.mark.asyncio
async def test_create_valid_to_in_past_rejected_422(http_client):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "name": "already_expired",
        "service": "jira",
        "scope": "personal",
        "secret": "s",
        "valid_to": _iso(past),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_valid_from_after_valid_to_rejected_422(http_client):
    later = datetime.now(timezone.utc) + timedelta(days=10)
    earlier = datetime.now(timezone.utc) + timedelta(days=5)
    payload = {
        "name": "inverted_window",
        "service": "jira",
        "scope": "personal",
        "secret": "s",
        "valid_from": _iso(later),
        "valid_to": _iso(earlier),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422, resp.text


# ── Reveal vs validity window ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reveal_before_valid_from_returns_410_not_yet_valid(http_client):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "nyv",
            "service": "jira",
            "scope": "personal",
            "secret": "s",
            "valid_from": _iso(future),
            "valid_to": _iso(far_future),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred_id}/reveal")
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body["error_code"] == "SECRET_NOT_YET_VALID"
    assert "valid_from" in body.get("details", {})


@pytest.mark.asyncio
async def test_reveal_within_validity_window_returns_200(http_client):
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "in_window",
            "service": "jira",
            "scope": "personal",
            "secret": "my-token",
            "valid_to": _iso(far_future),
        },
    )
    cred_id = create_resp.json()["id"]
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred_id}/reveal")
    assert resp.status_code == 200, resp.text
    import base64
    assert base64.b64decode(resp.json()["secret_b64"]).decode() == "my-token"


@pytest.mark.asyncio
async def test_reveal_after_valid_to_returns_410_expired(http_client, adb):
    """Создаём через repo напрямую с valid_to в прошлом — pydantic-валидатор не
    пропустил бы payload, но эмулируем ситуацию «было валидно, теперь истекло»."""
    from src.services import secrets_service

    cred_id = "cred_expired00000000000000000001"
    envelope = secrets_service.encrypt(
        "my-token", aad=secrets_service.aad_for_credential(cred_id)
    )
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name="was_valid",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        owner_user_dept_id=OWNER_DEPT,
        login="alice",
        secret_encrypted=envelope,
        status="active",
        created_by=OWNER_ID,
        valid_from=None,
        valid_to=past,
    )
    await adb.commit()
    resp = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body["error_code"] == "SECRET_EXPIRED"
    assert "valid_to" in body.get("details", {})


# ── Metadata GET ignores validity ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_metadata_after_expiration_returns_200(http_client, adb):
    from src.services import secrets_service

    cred_id = "cred_metaexp000000000000000000001"
    envelope = secrets_service.encrypt(
        "x", aad=secrets_service.aad_for_credential(cred_id)
    )
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name="meta_expired",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        owner_user_dept_id=OWNER_DEPT,
        login=None,
        secret_encrypted=envelope,
        status="active",
        created_by=OWNER_ID,
        valid_from=None,
        valid_to=past,
    )
    await adb.commit()
    # GET карточки — должен вернуть 200 + metadata, без ошибки expiration.
    resp = await http_client.get(f"/api/secret/v1/credentials/{cred.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid_to"] is not None
    # И в списке metadata тоже доступен.
    list_resp = await http_client.get("/api/secret/v1/credentials")
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert cred.id in ids


# ── Update extends validity ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_extends_valid_to_allows_reveal_again(http_client, adb):
    from src.services import secrets_service

    cred_id = "cred_extend000000000000000000001"
    envelope = secrets_service.encrypt(
        "my-token", aad=secrets_service.aad_for_credential(cred_id)
    )
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name="to_extend",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        owner_user_dept_id=OWNER_DEPT,
        login="alice",
        secret_encrypted=envelope,
        status="active",
        created_by=OWNER_ID,
        valid_from=None,
        valid_to=past,
    )
    await adb.commit()
    # Перед продлением — 410 EXPIRED.
    pre = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    assert pre.status_code == 410
    assert pre.json()["error_code"] == "SECRET_EXPIRED"

    # Продлеваем valid_to в будущее.
    new_to = datetime.now(timezone.utc) + timedelta(days=30)
    patch = await http_client.patch(
        f"/api/secret/v1/credentials/{cred.id}",
        json={"valid_to": _iso(new_to)},
    )
    assert patch.status_code == 200, patch.text

    # Reveal снова работает.
    reveal_throttle._reset_for_tests()
    post = await http_client.post(f"/api/secret/v1/credentials/{cred.id}/reveal")
    assert post.status_code == 200, post.text


# ── Guest list hides valid_from / valid_to ────────────────────────────────


@pytest.mark.asyncio
async def test_guest_list_does_not_expose_validity_fields(http_client, adb):
    """Гость видит только id/name/service/scope/visible_to_dept. valid_from /
    valid_to гостю НЕ отдаются — это metadata-leak."""
    from src.services import secrets_service

    cred_id = "cred_guestv000000000000000000001"
    envelope = secrets_service.encrypt(
        "x", aad=secrets_service.aad_for_credential(cred_id)
    )
    future = datetime.now(timezone.utc) + timedelta(days=10)
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name="dept_visible",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id=OWNER_DEPT,
        owner_user_dept_id=None,
        login=None,
        secret_encrypted=envelope,
        status="active",
        created_by=OWNER_ID,
        visible_to_dept=True,
        valid_from=None,
        valid_to=future,
    )
    await adb.commit()

    _set_identity(_identity(roles=["guest"]))
    resp = await http_client.get("/api/secret/v1/credentials")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # Гостю не должна светиться экспирация.
    matching = [it for it in items if it["id"] == cred.id]
    assert matching, "guest должен видеть visible_to_dept-кред'у своего dep'а"
    row = matching[0]
    assert "valid_from" not in row
    assert "valid_to" not in row
    assert set(row.keys()) == {"id", "name", "service", "scope", "visible_to_dept"}
