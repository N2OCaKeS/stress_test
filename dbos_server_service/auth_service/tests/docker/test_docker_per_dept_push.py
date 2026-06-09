"""Per-dept push enforcement для Docker registry.

Bench:
    * pull анонимно → ok, если pull_policy='all' на registry;
    * pull с правом → ok (registry.<name>.pull = subject в pull_user_ids
      или policy=all);
    * push в свой registry с правом → ok;
    * push в чужой registry (dept_a → dep_b) → 403 PUSH_DEPT_MISMATCH;
    * push в свой registry БЕЗ права → action соут-omit'нут, 200;
    * push без auth header → анонимная ветка не выдаётся, 401.
"""


import jwt as _jwt
import pytest_asyncio

from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id
from tests._helpers.http import _basic  # noqa: F401 — общий helper


TOKEN_URL = "/api/auth/v1/docker/token"


def _decode_access(token: str) -> list[dict]:
    return _jwt.decode(token, options={"verify_signature": False}).get("access", [])


@pytest_asyncio.fixture()
async def registry_dept_a(db, dept_a, user_a):
    """Registry, принадлежащий dept_a; user_a имеет и pull, и push право."""
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[user_a.id],
        push_user_ids=[user_a.id],
    )
    db.add(cfg)
    await db.flush()
    return cfg


@pytest_asyncio.fixture()
async def registry_dept_b(db, dept_b):
    """Registry, принадлежащий dept_b. Пустые pull/push списки — никто не пушит."""
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_b.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[],
        push_user_ids=[],
    )
    db.add(cfg)
    await db.flush()
    return cfg


# ── 1. Anonymous pull ────────────────────────────────────────────────────────

async def test_anonymous_pull_ok(client, dept_a, registry_dept_a):
    """Без auth header, scope=pull → 200 c pull в access (pull_policy='all')."""
    resp = await client.get(
        TOKEN_URL,
        params={"service": "registry.test", "scope": f"repository:{dept_a.name}/image:pull"},
    )
    assert resp.status_code == 200, resp.text
    access = _decode_access(resp.json()["access_token"])
    assert any("pull" in entry.get("actions", []) for entry in access)


# ── 2. Authenticated pull with permission ────────────────────────────────────

async def test_pull_with_permission_ok(client, dept_a, user_a, registry_dept_a):
    """user_a имеет docker_registry.dept_a.pull → 200 c pull."""
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": f"repository:{dept_a.name}/image:pull"},
    )
    assert resp.status_code == 200, resp.text
    access = _decode_access(resp.json()["access_token"])
    assert any("pull" in entry.get("actions", []) for entry in access)


# ── 3. Push to own registry with permission ──────────────────────────────────

async def test_push_to_own_registry_ok(client, dept_a, user_a, registry_dept_a):
    """user_a живёт в dept_a, имеет docker_registry.dept_a.push → push выдан."""
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": f"repository:{dept_a.name}/image:push"},
    )
    assert resp.status_code == 200, resp.text
    access = _decode_access(resp.json()["access_token"])
    assert any("push" in entry.get("actions", []) for entry in access)


# ── 4. Cross-dept push → 403 PUSH_DEPT_MISMATCH ──────────────────────────────

async def test_push_to_foreign_registry_denied(
    client, db, dept_a, dept_b, user_a, registry_dept_b,
):
    """user_a (dept_a) пытается push в registry dep_b → 403 PUSH_DEPT_MISMATCH."""
    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": f"repository:{dept_b.name}/image:push"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "PUSH_DEPT_MISMATCH"


# ── 5. Push без права в своём отделе — soft-omit ─────────────────────────────

async def test_push_without_permission_in_own_dept_omitted(
    client, db, dept_a, user_a,
):
    """user_a в dept_a, registry dept_a существует, НО user_a не в push_user_ids
    → ответ 200, в access нет push."""
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[user_a.id],
        push_user_ids=[],  # право не выдано
    )
    db.add(cfg)
    await db.flush()

    resp = await client.get(
        TOKEN_URL,
        headers=_basic("t_user_a", "User12345678!"),
        params={"service": "registry.test", "scope": f"repository:{dept_a.name}/image:push"},
    )
    assert resp.status_code == 200, resp.text
    access = _decode_access(resp.json()["access_token"])
    assert all("push" not in entry.get("actions", []) for entry in access)


# ── 6. Anonymous + scope с push → 401 ────────────────────────────────────────

async def test_anonymous_push_returns_401(client, dept_a, registry_dept_a):
    """Без auth header + scope=push → анон-ветка не активируется, 401."""
    resp = await client.get(
        TOKEN_URL,
        params={"service": "registry.test", "scope": f"repository:{dept_a.name}/image:push"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error_code"] == "MISSING_CREDENTIALS"
