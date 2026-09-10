"""HTTP-путь для нового scope `service` — /credentials CRUD + reveal.

`service` физически принадлежит отделу (та же форма владения, что
`department`) и управляется тем же кругом лиц (dep_admin / admin
secret_service своего dep'а). Единственное отличие — платформенный
сервис-бот (`Identity.is_service_bot=True`) получает read/reveal ЛЮБОГО
отдела без ручного DeptGrant. Обычная HTTP-фикстура (`http_client`/
`_set_identity`) — как в `test_credentials_endpoints.py`.
"""

from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app
from src.services import reveal_throttle
from tests._helpers import b64

OWNER_ID = "usr_owner000000000000000000000001"
OWNER_DEPT = "dep_owner00000000000000000000001"
OTHER_DEPT = "dep_other00000000000000000000001"
BOT_SYSTEM_DEPT = "dep_system0000000000000000000001"


def _identity(
    *,
    user_id: str = OWNER_ID,
    actor_type: str = "user",
    department_id: str | None = OWNER_DEPT,
    roles: list[str] | None = None,
    platform_role: str | None = None,
    is_service_bot: bool = False,
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
        is_service_bot=is_service_bot,
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
    app.dependency_overrides[get_identity] = lambda: _identity(
        roles=["admin"], platform_role="department_admin"
    )
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


# ── create: тот же ролевой гейт, что у department ────────────────────────────


@pytest.mark.asyncio
async def test_create_service_credential_by_dept_admin_ok(http_client):
    payload = {
        "name": "zephyr_bot",
        "service": "zephyr",
        "scope": "service",
        "secret_b64": b64("svc-secret"),
        "owner_dept_id": OWNER_DEPT,
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "service"
    assert body["owner_dept_id"] == OWNER_DEPT
    assert body["owner_user_id"] is None


@pytest.mark.asyncio
async def test_create_service_without_owner_dept_id_rejected(http_client):
    payload = {
        "name": "x",
        "service": "zephyr",
        "scope": "service",
        "secret_b64": b64("s"),
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_service_credential_by_plain_reader_denied(http_client):
    """Тот же гейт, что у department: guest/reader без admin-роли и без
    department_admin — не может завести service-креду."""
    _set_identity(_identity(roles=["reader"], platform_role=None))
    payload = {
        "name": "zephyr_bot2",
        "service": "zephyr",
        "scope": "service",
        "secret_b64": b64("svc-secret"),
        "owner_dept_id": OWNER_DEPT,
    }
    resp = await http_client.post("/api/secret/v1/credentials", json=payload)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "CREDENTIAL_ACCESS_DENIED"


# ── reveal: сервис-бот видит ЛЮБОЙ отдел, обычный бот — только свой ──────────


@pytest.mark.asyncio
async def test_service_bot_reveals_credential_of_foreign_department(http_client):
    plaintext = "zephyr-token-value"
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "zephyr_prod",
            "service": "zephyr",
            "scope": "service",
            "secret_b64": b64(plaintext),
            "owner_dept_id": OWNER_DEPT,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    # testing_service-бот: живёт в системном отделе, роль в secret_service —
    # системный guest, но `is_service_bot=True` — universal override.
    _set_identity(
        _identity(
            user_id="bot_testing_service00000001",
            actor_type="bot",
            department_id=BOT_SYSTEM_DEPT,
            roles=["guest"],
            platform_role=None,
            is_service_bot=True,
        )
    )
    reveal_resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred_id}/reveal"
    )
    assert reveal_resp.status_code == 200, reveal_resp.text
    decoded = base64.b64decode(reveal_resp.json()["secret_b64"]).decode("utf-8")
    assert decoded == plaintext


@pytest.mark.asyncio
async def test_ordinary_bot_cannot_reveal_foreign_department_service_cred(http_client):
    """Тот же guest-бот, но БЕЗ `is_service_bot` — доступ закрыт (замаскирован
    под 404, как обычный cross-dept miss)."""
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "zephyr_prod2",
            "service": "zephyr",
            "scope": "service",
            "secret_b64": b64("value2"),
            "owner_dept_id": OWNER_DEPT,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    _set_identity(
        _identity(
            user_id="bot_ordinary00000000000001",
            actor_type="bot",
            department_id=OTHER_DEPT,
            roles=["guest"],
            platform_role=None,
            is_service_bot=False,
        )
    )
    reveal_resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred_id}/reveal"
    )
    assert reveal_resp.status_code == 404, reveal_resp.text


@pytest.mark.asyncio
async def test_service_bot_cannot_write_service_credential(http_client):
    """Сервис-бот получает только read/reveal — PATCH остаётся закрыт."""
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "zephyr_prod3",
            "service": "zephyr",
            "scope": "service",
            "secret_b64": b64("value3"),
            "owner_dept_id": OWNER_DEPT,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    _set_identity(
        _identity(
            user_id="bot_testing_service00000002",
            actor_type="bot",
            department_id=BOT_SYSTEM_DEPT,
            roles=["guest"],
            platform_role=None,
            is_service_bot=True,
        )
    )
    resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred_id}",
        json={"secret_b64": b64("hacked")},
    )
    assert resp.status_code in (403, 404), resp.text


@pytest.mark.asyncio
async def test_owner_dept_admin_still_manages_service_credential(http_client):
    """Владелец (dep_admin своего dep'а) продолжает управлять service-кред'ой
    штатно — новый scope ничего не отбирает у обычного CRUD-пути."""
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "zephyr_prod4",
            "service": "zephyr",
            "scope": "service",
            "secret_b64": b64("value4"),
            "owner_dept_id": OWNER_DEPT,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    reveal_resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred_id}/reveal"
    )
    assert reveal_resp.status_code == 200, reveal_resp.text

    patch_resp = await http_client.patch(
        f"/api/secret/v1/credentials/{cred_id}",
        json={"login": "svc-login"},
    )
    assert patch_resp.status_code == 200, patch_resp.text


# ── DeptGrant остаётся закрыт для service (только cross_department) ─────────


@pytest.mark.asyncio
async def test_dept_grant_not_applicable_to_service_scope(http_client):
    """`service` не заводит DeptGrant — универсальный доступ идёт через
    `is_service_bot`, не через ручную настройку гранта на пару отделов."""
    create_resp = await http_client.post(
        "/api/secret/v1/credentials",
        json={
            "name": "zephyr_prod5",
            "service": "zephyr",
            "scope": "service",
            "secret_b64": b64("value5"),
            "owner_dept_id": OWNER_DEPT,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    resp = await http_client.post(
        f"/api/secret/v1/credentials/{cred_id}/dept-grants",
        json={"recipient_dept_id": OTHER_DEPT},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "DEPT_GRANT_NOT_APPLICABLE"
