"""Coverage gap для docker_registry_service: deny-audits, anonymous edges,
resolve-NOT_FOUND.

Фокус — ветки, которые не покрывали ни `test_docker_token.py`, ни
`test_docker_per_dept_push.py`, ни `test_docker_bot_lockout.py`.
"""

import jwt as _jwt
import pytest_asyncio

from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id
from tests._helpers.http import _basic  # noqa: F401 — общий helper
from datetime import timedelta
from src.utils.time import utcnow

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"


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


# ── push_denied / pull_denied audit shape ────────────────────────────

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
            headers=_basic("t_user_a", "User12345678!"),
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
            headers=_basic("t_user_a", "User12345678!"),
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


# ── anonymous pull on restricted registry — silent omit ───────────────

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


# ── _resolve_registry → REGISTRY_NOT_FOUND ───────────────────────────

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
            headers=_basic("t_user_a", "User12345678!"),
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


# ── empty scope (docker login без последующего pull/push) ──────────

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
            headers=_basic("t_user_a", "User12345678!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert access == []
        payload = _jwt.decode(
            resp.json()["access_token"], options={"verify_signature": False},
        )
        assert payload["sub"] == user_a.id


# ── subject_type='bot_token' в failure-audit ─────────────────────────


class TestAuthFailureAuditSubjectType:
    async def test_invalid_bot_token_audit_has_subject_type_bot_token(
        self, client, admin_token, dept_a, registry_a_pull_all, monkeypatch,
    ):
        """Битый bot-токен с правильным username бота → docker.token_issued
        failure с subject_type='bot_token'. Симметрия c PAT/password ветками
        (`test_docker_registry_auth_paths.py` покрывает их, bot-канал — нет).
        SOC отделяет brute-force через docker auth по типу субъекта."""
        # Заводим бота, чтобы username резолвился, но шлём garbage-token.
        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "audit_subj_bot",
                "department_id": dept_a.id,
                "allowed_services": [],
            },
        )
        assert bot_resp.status_code == 201, bot_resp.text

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("audit_subj_bot", "dbos_bot_garbage_xyz"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

        events = [e for e in captured if e["action"] == "docker.token_issued"]
        assert events, captured
        # Берём последний failure: lockout-инкременты выше не должны мешать.
        failures = [e for e in events if e.get("status") == "failure"]
        assert failures, events
        ev = failures[-1]
        assert ev["details"]["subject_type"] == "bot_token"
        assert ev["details"]["reason"] == "invalid_credentials"


# ── PAT для inactive юзера: ветка `user and user.is_active` ──────────────────


class TestPatInactiveUser:
    async def test_valid_pat_for_inactive_user_denied(
        self, client, admin_token, user_a, user_a_token, dept_a,
        registry_a_pull_all, db,
    ):
        """Валидный PAT, но user переведён в неактивное состояние →
        401 INVALID_CREDENTIALS. Source: `_authenticate_subject` line ~269
        (`if user and user.is_active`). Симметричный кейс — PAT-expired —
        покрыт в `test_docker_registry_auth_paths::test_expired_pat_denied`."""
        pat_raw = (await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "inactive_user_pat", "allowed_services": ["service_x"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )).json()["token"]

        # Деактивируем юзера напрямую в БД.
        from sqlalchemy import update
        from src.models import User
        await db.execute(
            update(User).where(User.id == user_a.id).values(
                is_active=False, status="blocked",
            )
        )
        await db.commit()

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", pat_raw),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


# ── PAT side-channel masking: user locked → generic INVALID_CREDENTIALS ──────


class TestPatLockoutSideChannel:
    async def test_valid_pat_when_user_locked_returns_invalid_credentials(
        self, client, admin_token, user_a, user_a_token, dept_a,
        registry_a_pull_all, db,
    ):
        """Валидный PAT, но у юзера активен password-lockout → 401
        INVALID_CREDENTIALS (не ACCOUNT_TEMPORARILY_LOCKED). Side-channel
        masking: держатель PAT'а не должен узнавать факт lockout'а юзера
        через docker-канал. Source: `_authenticate_subject` line ~276-281."""
        pat_raw = (await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "locked_user_pat", "allowed_services": ["service_x"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )).json()["token"]

        # Ставим locked_until в будущем — имитируем password-lockout.
        from datetime import datetime, timezone

        from sqlalchemy import update

        from src.models import User
        future_lock = datetime.now(timezone.utc) + timedelta(minutes=15)
        await db.execute(
            update(User).where(User.id == user_a.id).values(
                locked_until=future_lock, failed_login_attempts=5,
            )
        )
        await db.commit()

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", pat_raw),
            params={"service": "registry.test"},
        )
        # Generic 401 INVALID_CREDENTIALS, не 429 ACCOUNT_TEMPORARILY_LOCKED.
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


# ── REGISTRY_DISABLED для pull-операции (не push) ────────────────────────────


class TestRegistryDisabledPull:
    async def test_pull_on_disabled_registry_emits_disabled_audit(
        self, client, user_a, dept_a, db, monkeypatch,
    ):
        """user_a залогинен; registry dept_a существует, но `is_enabled=False`
        → docker.pull_denied reason=REGISTRY_DISABLED. Push-кейс с DISABLED
        косвенно идёт через legacy_only guard, pull-ветку покрывает
        отдельная строчка в issue_token (line ~585-600)."""
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_a.id,
            is_enabled=False,
            pull_policy="all",
            pull_user_ids=[],
            push_user_ids=[],
        )
        db.add(cfg)
        await db.flush()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:pull",
            },
        )
        # Использован сценарий с `<dept>/<image>` scope, не legacy — guard
        # legacy_only_scope не активируется, идём прямо к pull-обработчику.
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("pull" not in e.get("actions", []) for e in access)

        pull_denied = [e for e in captured if e["action"] == "docker.pull_denied"]
        assert pull_denied, captured
        ev = pull_denied[-1]
        assert ev["details"]["reason"] == "REGISTRY_DISABLED"
        assert ev["details"]["registry_name"] == dept_a.name


# ── PUSH_DEPT_MISMATCH audit shape: cross-dept push дет.aмерные поля ────────


class TestPushDeptMismatchAuditShape:
    async def test_cross_dept_push_audit_includes_owner_and_caller_dept_ids(
        self, client, user_a, dept_a, dept_b, db, monkeypatch,
    ):
        """user_a (dept_a) → push в registry dep_b → 403 PUSH_DEPT_MISMATCH
        + audit с registry_owner_dept_id=dept_b.id и caller_dept_id=dept_a.id.
        SIEM-правило коррелирует cross-dept push attempts по этим полям;
        отсутствие любого делает запись бесполезной для расследования."""
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

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_b.name}/image:push",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PUSH_DEPT_MISMATCH"

        push_denied = [e for e in captured if e["action"] == "docker.push_denied"]
        assert push_denied, captured
        ev = push_denied[-1]
        details = ev["details"]
        assert details["reason"] == "PUSH_DEPT_MISMATCH"
        assert details["registry_owner_dept_id"] == dept_b.id
        assert details["caller_dept_id"] == dept_a.id
        assert details["registry_name"] == dept_b.name
