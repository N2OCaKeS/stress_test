"""auth_service: точечное покрытие непокрытых веток docker_registry_service.

Целевые ветки:
  - docker_registry_service._parse_scope: сегменты < 3 пропускаются, пустая строка
  - docker_registry_service._parse_registry_name: без слэша → None, со слэшем → левая часть
  - docker_registry_service.update_config: PULL_USERS_REQUIRED via PATCH,
    dept_admin cross-dept → DEPARTMENT_ACCESS_DENIED
  - docker_registry_service.create_or_replace_config: несуществующий dept → DEPARTMENT_NOT_FOUND
  - docker_registry_service.delete_config: dept_admin cross-dept → DEPARTMENT_ACCESS_DENIED
  - docker_registry_service.issue_token: docker.token_issued failure audit details (NO_CFG/DISABLED)
  - user_service.reset_password: ветка elif actor_role is None с DA + cross-dept
  - audit_service._enrich_details: ctx.extra поля протекают в details
"""

import pytest
import pytest_asyncio

from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id
from tests._helpers.http import _basic  # noqa: F401 — общий helper


CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
TOKEN_URL = "/api/auth/v1/docker/token"
USERS_URL = "/api/auth/v1/users"


def _capture_audit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    import src.services.audit_service as audit_mod
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


# ── _parse_scope: unit-тесты на приватную функцию ─────────────────────────────

class TestParseScope:
    def test_empty_string_returns_empty_list(self):
        """Пустая строка scope → пустой список entries."""
        from src.services.docker_registry_service import _parse_scope
        assert _parse_scope("") == []

    def test_short_scope_two_segments_ignored(self):
        """scope без actions-сегмента (`type:name`) → пропускается (< 3 сегментов)."""
        from src.services.docker_registry_service import _parse_scope
        result = _parse_scope("repository:myapp")
        assert result == []

    def test_short_scope_one_segment_ignored(self):
        from src.services.docker_registry_service import _parse_scope
        assert _parse_scope("repository") == []

    def test_valid_scope_three_segments(self):
        from src.services.docker_registry_service import _parse_scope
        result = _parse_scope("repository:myapp:pull")
        assert len(result) == 1
        assert result[0]["type"] == "repository"
        assert result[0]["name"] == "myapp"
        assert result[0]["actions"] == ["pull"]

    def test_scope_with_colon_in_resource_name(self):
        """Имя ресурса может содержать двоеточие (registry-prefix:image)."""
        from src.services.docker_registry_service import _parse_scope
        result = _parse_scope("repository:dept:image:pull,push")
        assert len(result) == 1
        assert result[0]["name"] == "dept:image"
        assert "pull" in result[0]["actions"]
        assert "push" in result[0]["actions"]

    def test_multi_scope_mixed_valid_and_short(self):
        """Несколько записей: валидная + короткая → только валидная попадает."""
        from src.services.docker_registry_service import _parse_scope
        result = _parse_scope("repository:myapp:pull repository:bare")
        assert len(result) == 1
        assert result[0]["name"] == "myapp"

    def test_scope_multiple_actions_split_correctly(self):
        from src.services.docker_registry_service import _parse_scope
        result = _parse_scope("repository:img:pull,push,delete")
        assert result[0]["actions"] == ["pull", "push", "delete"]


# ── _parse_registry_name: unit-тесты ─────────────────────────────────────────

class TestParseRegistryName:
    def test_no_slash_returns_none(self):
        """Имя без слэша → registry_name не задан → None."""
        from src.services.docker_registry_service import _parse_registry_name
        assert _parse_registry_name("myapp") is None

    def test_with_slash_returns_first_component(self):
        """`dept_name/image` → `dept_name`."""
        from src.services.docker_registry_service import _parse_registry_name
        assert _parse_registry_name("dept_alpha/nginx") == "dept_alpha"

    def test_multiple_slashes_returns_first_component(self):
        """Глубокий путь → только первая компонента."""
        from src.services.docker_registry_service import _parse_registry_name
        assert _parse_registry_name("dept_alpha/base/nginx:latest") == "dept_alpha"

    def test_empty_string(self):
        from src.services.docker_registry_service import _parse_registry_name
        assert _parse_registry_name("") is None


# ── update_config (PATCH): PULL_USERS_REQUIRED и dept_admin cross-dept ────────

class TestUpdateConfigEdgeCases:
    async def test_patch_restricted_with_empty_pull_user_ids_raises(
        self, client, admin_token, dept_a, db,
    ):
        """PATCH pull_policy=restricted с пустым pull_user_ids → 403 PULL_USERS_REQUIRED."""
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

        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "restricted", "pull_user_ids": []},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PULL_USERS_REQUIRED"

    async def test_patch_dept_admin_cross_dept_forbidden(
        self, client, dept_admin_a_token, dept_b, db,
    ):
        """dept_admin_a пытается PATCH docker config dept_b → 403 DEPARTMENT_ACCESS_DENIED."""
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

        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_b.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"pull_policy": "all"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── create_or_replace_config (PUT): несуществующий dept → DEPARTMENT_NOT_FOUND

class TestCreateOrReplaceConfigNotFound:
    async def test_put_nonexistent_dept_returns_404(self, client, admin_token):
        """PUT с несуществующим department_id → 404 DEPARTMENT_NOT_FOUND."""
        fake_dept_id = _new_id("dep_")
        resp = await client.put(
            CONFIG_URL.format(dept_id=fake_dept_id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_NOT_FOUND"


# ── delete_config (DELETE): dept_admin cross-dept ─────────────────────────────

class TestDeleteConfigDeptAdmin:
    async def test_delete_dept_admin_cross_dept_forbidden(
        self, client, dept_admin_a_token, dept_b, db,
    ):
        """dept_admin_a пытается DELETE docker config dept_b → 403 DEPARTMENT_ACCESS_DENIED."""
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

        resp = await client.delete(
            CONFIG_URL.format(dept_id=dept_b.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── issue_token: docker.token_issued failure audit details (NO_CFG/DISABLED) ──

class TestIssueTokenFailureAuditDetails:
    async def test_no_config_emits_token_issued_failure_no_cfg(
        self, client, user_a, dept_a, admin_token, monkeypatch,
    ):
        """Пользователь без docker registry config → docker.token_issued failure reason=NO_CFG."""
        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, f"docker.token_issued failure не эмитнут: {captured}"
        details = failures[0].get("details", {})
        assert details.get("reason") == "NO_CFG"

    async def test_disabled_config_emits_token_issued_failure_disabled(
        self, client, user_a, dept_a, db, admin_token, monkeypatch,
    ):
        """Отключённый registry → docker.token_issued failure reason=DISABLED."""
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
            headers=_basic("t_user_a", "User1234!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 403, resp.text

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, f"docker.token_issued failure не эмитнут: {captured}"
        details = failures[0].get("details", {})
        assert details.get("reason") == "DISABLED"


# ── reset_password: ветка elif actor_role is None ────────────────────────────

class TestResetPasswordActorRoleNone:
    async def test_dept_admin_cross_dept_no_actor_role_raises(
        self, db, dept_a, dept_b,
    ):
        """reset_password с actor_role=None для DA из dept_b, target из dept_a → 403.

        Ветка `elif actor_role is None` в reset_password: смотрит в БД за ролью актора
        и проверяет cross-dept. Тест без HTTP-слоя (service-level).
        """
        from src.core.constants import PlatformRole
        from src.core.exceptions import AuthorizationError
        from src.core.security import hash_password
        from src.models import User
        from src.services import user_service
        from src.utils.ids import _new_id

        # target в dept_a
        target = User(
            id=_new_id("usr_"),
            username="rp_none_target",
            password_hash=hash_password("OldPass1!"),
            department_id=dept_a.id,
            status="active",
            is_active=True,
        )
        db.add(target)

        # actor — dept_admin в dept_b
        actor = User(
            id=_new_id("usr_"),
            username="rp_none_da_b",
            password_hash=hash_password("Admin1234!"),
            department_id=dept_b.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            status="active",
            is_active=True,
        )
        db.add(actor)
        await db.flush()

        with pytest.raises(AuthorizationError) as exc:
            await user_service.reset_password(
                db=db,
                actor_id=actor.id,
                actor_role=None,
                user_id=target.id,
                new_password="NewPass1!",
            )
        # Переехало на _dept_guard, error_code теперь
        # USER_RESET_PASSWORD_FORBIDDEN (raised из helper'а).
        assert exc.value.error_code in {"DEPARTMENT_ISOLATION", "USER_RESET_PASSWORD_FORBIDDEN"}

    async def test_dept_admin_same_dept_no_actor_role_allowed(
        self, db, dept_a,
    ):
        """reset_password с actor_role=None для DA из dept_a, target из dept_a → OK."""
        from src.core.constants import PlatformRole
        from src.core.security import hash_password
        from src.models import User
        from src.services import user_service
        from src.utils.ids import _new_id

        target = User(
            id=_new_id("usr_"),
            username="rp_same_target",
            password_hash=hash_password("OldPass1!"),
            department_id=dept_a.id,
            status="active",
            is_active=True,
        )
        db.add(target)

        actor = User(
            id=_new_id("usr_"),
            username="rp_same_da_a",
            password_hash=hash_password("Admin1234!"),
            department_id=dept_a.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            status="active",
            is_active=True,
        )
        db.add(actor)
        await db.flush()

        # Не должен кидать исключение
        await user_service.reset_password(
            db=db,
            actor_id=actor.id,
            actor_role=None,
            user_id=target.id,
            new_password="NewPass1!",
        )


# ── audit_service._enrich_details: ctx.extra протекает в details ──────────────

class TestEnrichDetailsCtxExtra:
    def test_extra_fields_added_to_details(self, monkeypatch):
        """ctx.extra поля, добавленные через update_context, попадают в details."""
        from src.services import audit_context, audit_service

        captured_payloads: list[dict] = []

        async def fake_send(payload, url, api_key):
            captured_payloads.append(payload)

        monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)
        settings = audit_service.get_settings()
        monkeypatch.setattr(settings, "logging_service_url", "http://test", raising=False)
        monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

        ctx = audit_context.AuditContext(
            actor_id="usr_extra_test",
            request_id="req_extra",
        )
        ctx.extra["custom_key"] = "custom_value"
        token = audit_context.set_context(ctx)
        try:
            enriched = audit_service._enrich_details({"existing": "data"})
        finally:
            audit_context.reset_context(token)

        assert enriched.get("custom_key") == "custom_value"
        assert enriched.get("existing") == "data"

    def test_extra_fields_do_not_overwrite_explicit_details(self):
        """Явный ключ в details имеет приоритет над ctx.extra."""
        from src.services import audit_context, audit_service

        ctx = audit_context.AuditContext()
        ctx.extra["key"] = "from_extra"
        token = audit_context.set_context(ctx)
        try:
            enriched = audit_service._enrich_details({"key": "explicit"})
        finally:
            audit_context.reset_context(token)

        assert enriched["key"] == "explicit"

    def test_no_extra_context_passes_through(self):
        """Без ctx.extra — details возвращаются без добавлений."""
        from src.services import audit_context, audit_service

        ctx = audit_context.AuditContext()
        token = audit_context.set_context(ctx)
        try:
            enriched = audit_service._enrich_details({"hello": "world"})
        finally:
            audit_context.reset_context(token)

        assert enriched == {"hello": "world"}


# ── _resolve_registry: fallback_department_id=None ───────────────────────────

class TestResolveRegistryFallback:
    async def test_resolve_registry_no_name_no_dept_returns_none_none(self, db):
        """Без registry_name и без fallback_department_id → (None, None)."""
        from src.services.docker_registry_service import _resolve_registry

        dept, cfg = await _resolve_registry(db, registry_name=None, fallback_department_id=None)
        assert dept is None
        assert cfg is None

    async def test_resolve_registry_name_not_found_returns_none_none(self, db):
        """registry_name не совпадает ни с одним Department.name → (None, None)."""
        from src.services.docker_registry_service import _resolve_registry

        dept, cfg = await _resolve_registry(db, registry_name="ghost_dept_xyz", fallback_department_id=None)
        assert dept is None
        assert cfg is None

    async def test_resolve_registry_by_fallback_dept_returns_cfg(self, db, dept_a):
        """Без registry_name но с fallback_department_id → возвращает конфиг отдела."""
        from src.services.docker_registry_service import _resolve_registry

        cfg_row = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_a.id,
            is_enabled=True,
            pull_policy="all",
            pull_user_ids=[],
            push_user_ids=[],
        )
        db.add(cfg_row)
        await db.flush()

        dept, cfg = await _resolve_registry(db, registry_name=None, fallback_department_id=dept_a.id)
        assert dept is not None
        assert dept.id == dept_a.id
        assert cfg is not None
        assert cfg.department_id == dept_a.id
