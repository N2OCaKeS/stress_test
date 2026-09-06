"""Unit-тесты слоя авторизации `src/services/permissions.py`.

БД и репозиторий мокаются через monkeypatch — задача проверить именно логику
склейки identity + ENTITY_ACTIONS, а не SQL.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AuthorizationError
from src.dependencies.auth import SERVICE_NAME
from src.schemas.identity import IdentityContext
from src.services import permissions


def _identity(
    *,
    user_id: str = "usr_user",
    platform_role: str | None = None,
    roles: list[str] | None = None,
    department_id: str | None = "dep_a",
) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        username="t",
        department_id=department_id,
        allowed_services=[SERVICE_NAME],
        service_roles={SERVICE_NAME: roles or []},
        platform_role=platform_role,
    )


@pytest.fixture
def repo_stub(monkeypatch):
    """Подменяет асинхронные функции repo, чтобы тесты не лезли в БД.

    Stubs принимают ``department_id`` kwarg, чтобы соответствовать
    реальной сигнатуре repo. По умолчанию все grants трактуются как
    system-wide (department_id=None) — эти unit-тесты про role/action
    логику; department scoping проверяется интеграционным suite'ом
    (``tests/integration/test_custom_role_cross_dept_isolation.py``).
    """

    grants: dict[tuple[str, str, str], bool] = {}
    rows: dict[str, list[object]] = {}

    async def fake_has_action(db, entity_type, roles, action, department_id=None):
        return any((entity_type, r, action) in grants for r in roles)

    async def fake_effective_actions(db, entity_type, roles, department_id=None):
        role_set = set(roles)
        out: set[str] = set()
        for row in rows.get(entity_type, []):
            if row.role not in role_set:
                continue
            if row.department_id is None or row.department_id == department_id:
                out.add(row.action)
        return out

    monkeypatch.setattr(permissions.repo, "has_action", fake_has_action)
    monkeypatch.setattr(permissions.repo, "effective_actions", fake_effective_actions)

    class _Ctl:
        def grant(self, entity_type, role, action):
            grants[(entity_type, role, action)] = True

        def set_rows(self, entity_type, items):
            rows[entity_type] = items

    return _Ctl()


# ── account_admin bypass REMOVED (admin-plane separation) ───────────────────

class TestAccountAdminNoLongerBypassesMatrix:
    """Регрессия admin-plane separation. Раньше ``has_action`` возвращал
    ``True`` для ``account_admin`` без consulting матрицы — bypass убран.
    Сам endpoint-уровень для ``account_admin`` всё равно отбивается
    ``platform_admin_guard`` middleware ДО этого слоя, но defence-in-depth:
    матрица должна оставаться единственным источником истины. См.
    ``src/services/permissions.py`` docstring + integration test
    ``tests/integration/test_platform_admin_block.py``.
    """

    async def test_account_admin_without_roles_denied(self, repo_stub):
        """`account_admin` без service_roles → не имеет доступа через матрицу.

        Соответствует §7 модели: ``account_admin`` создаётся без
        департамента и без сервисных ролей — следовательно ``has_action``
        возвращает False.
        """
        identity = _identity(platform_role="account_admin", roles=[], department_id=None)
        assert await permissions.has_action(None, identity, "server", "delete") is False

    async def test_account_admin_require_action_raises(self, repo_stub):
        identity = _identity(platform_role="account_admin", roles=[], department_id=None)
        with pytest.raises(AuthorizationError) as exc:
            await permissions.require_action(None, identity, "server", "power_off")
        assert exc.value.error_code == "PERMISSION_DENIED"

    async def test_account_admin_effective_actions_returns_empty(self, repo_stub):
        """Раньше возвращался полный whitelist для account_admin — теперь пустой."""
        identity = _identity(platform_role="account_admin", roles=[], department_id=None)
        got = await permissions.effective_actions(None, identity, "server")
        assert got == set()


# ── has_action / require_action ──────────────────────────────────────────────

class TestHasAction:
    async def test_no_service_roles_returns_false(self, repo_stub):
        identity = _identity(roles=[])
        assert await permissions.has_action(None, identity, "server", "view") is False

    async def test_no_grant_returns_false(self, repo_stub):
        identity = _identity(roles=["reader"])
        assert await permissions.has_action(None, identity, "server", "delete") is False

    async def test_matching_grant_returns_true(self, repo_stub):
        identity = _identity(roles=["operator"])
        repo_stub.grant("server", "operator", "power_on")
        assert await permissions.has_action(None, identity, "server", "power_on") is True

    async def test_multiple_roles_union(self, repo_stub):
        """Если хотя бы одна роль из списка имеет grant — действие разрешено."""
        identity = _identity(roles=["reader", "operator"])
        repo_stub.grant("server", "operator", "update")
        assert await permissions.has_action(None, identity, "server", "update") is True


class TestRequireAction:
    async def test_denies_when_no_role(self, repo_stub):
        identity = _identity(roles=[])
        with pytest.raises(AuthorizationError) as exc:
            await permissions.require_action(None, identity, "server", "delete")
        assert exc.value.error_code == "PERMISSION_DENIED"
        assert exc.value.details["entity_type"] == "server"
        assert exc.value.details["action"] == "delete"

    async def test_denies_when_role_lacks_action(self, repo_stub):
        identity = _identity(roles=["reader"])
        with pytest.raises(AuthorizationError):
            await permissions.require_action(None, identity, "server", "delete")

    async def test_passes_when_grant_exists(self, repo_stub):
        identity = _identity(roles=["admin"])
        repo_stub.grant("server", "admin", "delete")
        await permissions.require_action(None, identity, "server", "delete")


# ── effective_actions ────────────────────────────────────────────────────────

class TestEffectiveActions:
    async def test_no_roles_returns_empty_set(self, repo_stub):
        identity = _identity(roles=[])
        assert await permissions.effective_actions(None, identity, "server") == set()

    async def test_union_across_held_roles(self, repo_stub):
        """Результат — объединение actions всех ролей, что числятся за identity."""
        identity = _identity(roles=["reader", "operator"])

        class _Row:
            def __init__(self, role, action, department_id=None):
                self.role = role
                self.action = action
                # effective_actions фильтрует по department_id; для unit-тестов
                # трактуем rows как system-wide, если явно не задано.
                self.department_id = department_id

        repo_stub.set_rows("server", [
            _Row("reader", "view"),
            _Row("operator", "view"),
            _Row("operator", "power_on"),
            _Row("admin", "delete"),     # admin не у identity — должен быть отфильтрован
        ])
        got = await permissions.effective_actions(None, identity, "server")
        assert got == {"view", "power_on"}

    async def test_roles_not_in_db_yield_empty(self, repo_stub):
        identity = _identity(roles=["custom_role"])
        repo_stub.set_rows("server", [])  # пусто
        assert await permissions.effective_actions(None, identity, "server") == set()

    async def test_custom_role_from_other_dept_not_leaked(self, repo_stub):
        """Кастомная роль того же имени в чужом отделе не должна светиться
        в effective_actions у caller'а из dep_a (per-dept изоляция матрицы).
        """
        identity = _identity(roles=["custom_b"], department_id="dep_a")

        class _Row:
            def __init__(self, role, action, department_id=None):
                self.role = role
                self.action = action
                self.department_id = department_id

        repo_stub.set_rows("server", [
            _Row("custom_b", "view", department_id="dep_b"),
            _Row("custom_b", "list", department_id="dep_a"),
        ])
        got = await permissions.effective_actions(None, identity, "server")
        assert got == {"list"}


# ── Service-role isolation ────────────────────────────────────────────────────

class TestServiceRoleScoping:
    async def test_roles_for_other_service_are_ignored(self, repo_stub):
        """`identity.service_roles` хранит роли по всем сервисам, но
        permissions смотрит только на SERVICE_NAME."""
        identity = IdentityContext(
            user_id="usr_x",
            username="t",
            department_id="dep_a",
            allowed_services=["other_service"],
            service_roles={"other_service": ["admin"]},  # admin, но не в server_service
        )
        repo_stub.grant("server", "admin", "delete")
        assert await permissions.has_action(None, identity, "server", "delete") is False


# ── require_host_service_action ──────────────────────────────────────────────

class TestRequireHostServiceAction:
    """`require_host_service_action` — department_admin bypass OR matrix,
    оба условия дополнительно требуют совпадения `identity.department_id`
    с целевым `department_id` (см. docstring функции)."""

    async def test_department_admin_bypasses_matrix(self, repo_stub):
        identity = _identity(platform_role="department_admin", roles=[], department_id="dep_a")
        await permissions.require_host_service_action(None, identity, "dep_a", "host_service_manage")

    async def test_department_admin_of_other_department_denied(self, repo_stub):
        identity = _identity(platform_role="department_admin", roles=[], department_id="dep_b")
        with pytest.raises(AuthorizationError) as exc:
            await permissions.require_host_service_action(None, identity, "dep_a", "host_service_manage")
        assert exc.value.error_code == "PERMISSION_DENIED"

    async def test_matrix_admin_role_grants(self, repo_stub):
        identity = _identity(roles=["admin"], department_id="dep_a")
        repo_stub.grant("host_service", "admin", "host_service_manage")
        await permissions.require_host_service_action(None, identity, "dep_a", "host_service_manage")

    async def test_matrix_role_without_grant_denied(self, repo_stub):
        identity = _identity(roles=["reader"], department_id="dep_a")
        with pytest.raises(AuthorizationError):
            await permissions.require_host_service_action(None, identity, "dep_a", "host_service_manage")

    async def test_department_mismatch_denied_even_with_matching_grant(self, repo_stub):
        """Матрица разрешает identity's own department, но вызов с чужим
        `department_id` не должен пройти — `identity.department_id ==
        department_id` защищает от cross-department использования функции,
        даже если бы `has_action` сама по себе не заметила подмену."""
        identity = _identity(roles=["admin"], department_id="dep_a")
        repo_stub.grant("host_service", "admin", "host_service_manage")
        with pytest.raises(AuthorizationError):
            await permissions.require_host_service_action(None, identity, "dep_b", "host_service_manage")

    async def test_account_admin_denied(self, repo_stub):
        """account_admin: нет department_id (bypass ветка ложна) и нет
        service_roles в server_service (`has_action` возвращает False)."""
        identity = _identity(platform_role="account_admin", roles=[], department_id=None)
        with pytest.raises(AuthorizationError):
            await permissions.require_host_service_action(None, identity, "dep_a", "host_service_manage")

    async def test_control_action_is_independent_of_manage(self, repo_stub):
        """Грант на `host_service_manage` не даёт `host_service_control` — два
        разных action'а в матрице, как и задумано (см. `constants.py`)."""
        identity = _identity(roles=["admin"], department_id="dep_a")
        repo_stub.grant("host_service", "admin", "host_service_manage")
        with pytest.raises(AuthorizationError):
            await permissions.require_host_service_action(None, identity, "dep_a", "host_service_control")
