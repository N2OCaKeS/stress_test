"""Coverage gap для docker_registry_service: deny-audits, anonymous edges,
resolve-NOT_FOUND.

Фокус — ветки, которые не покрывали ни `test_docker_token.py`, ни
`test_docker_per_dept_push.py`, ни `test_docker_bot_lockout.py`.
"""

import base64

import jwt as _jwt
import pytest_asyncio

from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"


def _basic(username: str, password: str) -> dict[str, str]:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _decode_access(token: str) -> list[dict]:
    return _jwt.decode(token, options={"verify_signature": False}).get("access", [])


@pytest_asyncio.fixture()
async def registry_a_pull_all(db, dept_a, user_a):
    """Registry dept_a, pull_policy=all, user_a в push, без особых pull-id."""
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[],
        push_user_ids=[user_a.id],
    )
    db.add(cfg)
    await db.flush()
    return cfg


@pytest_asyncio.fixture()
async def registry_a_restricted(db, dept_a):
    """Registry dept_a, pull_policy=restricted, никого нет в pull_user_ids."""
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="restricted",
        pull_user_ids=[],
        push_user_ids=[],
    )
    db.add(cfg)
    await db.flush()
    return cfg


def _capture_audit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    import src.services.audit_service as audit_mod
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


# ── GAP-10: push_denied / pull_denied audit shape ────────────────────────────

class TestDeniedAuditShape:
    async def test_push_permission_denied_emits_audit_with_scope(
        self, client, user_a, dept_a, db, monkeypatch,
    ):
        """user_a в dept_a, registry dept_a с пустым push_user_ids → push omit,
        + docker.push_denied audit PUSH_PERMISSION_DENIED."""
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_a.id,
            is_enabled=True,
            pull_policy="all",
            pull_user_ids=[],
            push_user_ids=[],
        )
        db.add(cfg)
        await db.flush()

        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:push",
            },
        )
        # Soft-omit: 200, без push в access; audit denied всё равно эмитится.
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("push" not in e.get("actions", []) for e in access)

        push_denied = [e for e in captured if e["action"] == "docker.push_denied"]
        assert len(push_denied) == 1, captured
        ev = push_denied[0]
        assert ev["actor_id"] == user_a.id
        assert ev["details"]["reason"] == "PUSH_PERMISSION_DENIED"
        assert ev["details"]["registry_name"] == dept_a.name
        assert ev["target_type"] == "docker_registry"

    async def test_pull_permission_denied_emits_audit(
        self, client, user_a, dept_a, registry_a_restricted, monkeypatch,
    ):
        """user_a в dept_a, registry restricted, user_a НЕ в pull_user_ids
        → pull omit'нут + docker.pull_denied audit (PULL_PERMISSION_DENIED)."""
        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:pull",
            },
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("pull" not in e.get("actions", []) for e in access)

        pull_denied = [e for e in captured if e["action"] == "docker.pull_denied"]
        assert len(pull_denied) == 1, captured
        ev = pull_denied[0]
        assert ev["actor_id"] == user_a.id
        assert ev["details"]["reason"] == "PULL_PERMISSION_DENIED"


# ── GAP-11: anonymous pull on restricted registry — silent omit ───────────────

class TestAnonymousRestricted:
    async def test_anonymous_pull_on_restricted_silently_omitted(
        self, client, dept_a, registry_a_restricted, monkeypatch,
    ):
        """Анон pull на restricted registry → токен выдан, но без pull в access.
        Никаких docker.pull_denied на анона (он и так получит 401 от registry).
        """
        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:pull",
            },
        )
        # docker registry token endpoint выдаёт JWT даже анонимам — registry
        # сам отбьёт неавторизованный pull. Главное — нет 403, нет pull в access.
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("pull" not in e.get("actions", []) for e in access)

        # Для анона не должно быть denied-audit'а: subject_id=None, audit-шум не нужен.
        pull_denied = [e for e in captured if e["action"] == "docker.pull_denied"]
        assert pull_denied == [], pull_denied


# ── GAP-13: _resolve_registry → REGISTRY_NOT_FOUND ───────────────────────────

class TestResolveRegistryNotFound:
    async def test_push_to_nonexistent_registry_emits_registry_not_found(
        self, client, user_a, dept_a, registry_a_pull_all, monkeypatch,
    ):
        """scope ссылается на registry, у которого нет ни Department с таким
        name, ни конфига → docker.push_denied reason=REGISTRY_NOT_FOUND.
        """
        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={
                "service": "registry.test",
                "scope": "repository:ghost_registry_xyz/image:push",
            },
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("push" not in e.get("actions", []) for e in access)

        push_denied = [e for e in captured if e["action"] == "docker.push_denied"]
        assert len(push_denied) == 1, captured
        ev = push_denied[0]
        assert ev["details"]["reason"] == "REGISTRY_NOT_FOUND"
        assert ev["details"]["registry_name"] == "ghost_registry_xyz"


# ── GAP-16: empty scope (docker login без последующего pull/push) ──────────

class TestEmptyScopeBehavior:
    async def test_anonymous_empty_scope_returns_401(
        self, client, dept_a, registry_a_pull_all,
    ):
        """`docker login` без scope + без Basic → 401 MISSING_CREDENTIALS.
        Фиксируем поведение: анон-путь требует pull-scope, пустой scope
        не выдаёт токен.
        """
        resp = await client.get(TOKEN_URL, params={"service": "registry.test"})
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "MISSING_CREDENTIALS"

    async def test_authenticated_empty_scope_returns_token(
        self, client, user_a, dept_a, registry_a_pull_all,
    ):
        """`docker login` с Basic, но без scope → 200, JWT с пустым access.
        Это типовая первая фаза docker login: handshake без операций."""
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert access == []
        payload = _jwt.decode(
            resp.json()["access_token"], options={"verify_signature": False},
        )
        assert payload["sub"] == user_a.id
