"""HTTP-эндпоинты /api/secret/v1/permissions — каталог / список / grant / revoke."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.dependencies.auth import Identity, get_identity
from src.dependencies.db import get_db
from src.main import app

BASE = "/api/secret/v1/permissions"
DEPT_A = "dep_a000000000000000000000000001"


def _identity(
    *,
    user_id: str = "usr_admin00000000000000000000001",
    department_id: str | None = DEPT_A,
    roles: list[str] | None = None,
    platform_role: str | None = "department_admin",
    actor_type: str = "user",
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or []},
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
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _set_identity(identity: Identity) -> None:
    app.dependency_overrides[get_identity] = lambda: identity


@pytest.mark.asyncio
async def test_catalog_returns_secret_entity(http_client) -> None:
    resp = await http_client.get(f"{BASE}/catalog")
    assert resp.status_code == 200
    catalog = resp.json()
    assert {e["entity_type"] for e in catalog} == {"secret"}
    actions = {a["action"] for a in catalog[0]["actions"]}
    assert actions == {"read", "reveal", "write", "delete", "grant_acl", "grant_dept", "manage_status"}
    reveal = next(a for a in catalog[0]["actions"] if a["action"] == "reveal")
    assert reveal["sensitive"] is True


@pytest.mark.asyncio
async def test_grant_and_list_envelope(http_client) -> None:
    put = await http_client.put(f"{BASE}/secret/reader/read")
    assert put.status_code == 200
    body = put.json()
    assert body["role"] == "reader" and body["action"] == "read"

    lst = await http_client.get(f"{BASE}?role=reader")
    assert lst.status_code == 200
    env = lst.json()
    assert env["described"] is False
    assert env["total"] == len(env["items"])
    assert any(r["role"] == "reader" and r["action"] == "read" for r in env["items"])


@pytest.mark.asyncio
async def test_list_describe_enriches(http_client) -> None:
    await http_client.put(f"{BASE}/secret/reader/reveal")
    lst = await http_client.get(f"{BASE}?role=reader&describe=true")
    assert lst.status_code == 200
    env = lst.json()
    assert env["described"] is True
    row = next(r for r in env["items"] if r["action"] == "reveal")
    assert row["sensitive"] is True
    assert row["action_description"].strip()


@pytest.mark.asyncio
async def test_grant_idempotent_same_id(http_client) -> None:
    first = await http_client.put(f"{BASE}/secret/operator/write")
    second = await http_client.put(f"{BASE}/secret/operator/write")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_revoke_then_missing_404(http_client) -> None:
    await http_client.put(f"{BASE}/secret/reader/delete")
    d1 = await http_client.delete(f"{BASE}/secret/reader/delete")
    assert d1.status_code == 200
    d2 = await http_client.delete(f"{BASE}/secret/reader/delete")
    assert d2.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["guest", "admin"])
async def test_grant_system_role_409(http_client, role) -> None:
    resp = await http_client.put(f"{BASE}/secret/{role}/read")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_grant_invalid_action_422(http_client) -> None:
    resp = await http_client.put(f"{BASE}/secret/reader/power_on")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_entity_type_422(http_client) -> None:
    resp = await http_client.get(f"{BASE}/server")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_denied_403(http_client) -> None:
    _set_identity(_identity(roles=["reader"], platform_role=None))
    resp = await http_client.get(f"{BASE}/catalog")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cross_dept_grant_isolation_403(http_client) -> None:
    resp = await http_client.put(
        f"{BASE}/secret/reader/read",
        json={"target_department_id": "dep_other0000000000000000000001"},
    )
    assert resp.status_code == 403
