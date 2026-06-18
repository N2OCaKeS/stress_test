"""Unit-тесты per-account авторизации `permissions.has_account_action`.

Аддитивная модель: доступ к учётке = бланкетная роль ИЛИ прямой per-account
грант ИЛИ department_admin-bypass. repo и acl_repo мокаются — проверяем
именно склейку, не SQL.
"""

from __future__ import annotations

import pytest

from src.core.constants import ACCOUNT_ACL_ACTION_COLUMNS
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


class _Account:
    def __init__(self, account_id="acc_1", department_id="dep_a"):
        self.id = account_id
        self.department_id = department_id


class _Grant:
    """ACL-строка: только перечисленные действия True."""

    def __init__(self, actions: set[str]):
        for action, col in ACCOUNT_ACL_ACTION_COLUMNS.items():
            setattr(self, col, action in actions)


@pytest.fixture
def acl_stub(monkeypatch):
    """Подменяет repo.has_action (роль) и acl_repo.get_for_account_user (грант)."""
    role_grants: dict[tuple[str, str, str], bool] = {}
    acl_grant: dict[str, _Grant] = {}

    async def fake_has_action(db, entity_type, roles, action, department_id=None):
        return any((entity_type, r, action) in role_grants for r in roles)

    async def fake_get_for_account_user(db, account_id, user_id):
        return acl_grant.get(user_id)

    monkeypatch.setattr(permissions.repo, "has_action", fake_has_action)
    monkeypatch.setattr(
        permissions.acl_repo, "get_for_account_user", fake_get_for_account_user
    )

    class _Ctl:
        def role(self, entity_type, role, action):
            role_grants[(entity_type, role, action)] = True

        def grant(self, user_id, actions):
            acl_grant[user_id] = _Grant(set(actions))

    return _Ctl()


class TestHasAccountAction:
    async def test_blanket_role_grants(self, acl_stub):
        identity = _identity(roles=["operator"])
        acl_stub.role("server_account", "operator", "view_password")
        assert await permissions.has_account_action(
            None, identity, _Account(), "view_password"
        ) is True

    async def test_per_account_grant_without_role(self, acl_stub):
        """Пользователь без роли получает доступ через прямой грант."""
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"view_password"})
        assert await permissions.has_account_action(
            None, identity, _Account(), "view_password"
        ) is True

    async def test_grant_only_covers_granted_actions(self, acl_stub):
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"view"})
        assert await permissions.has_account_action(
            None, identity, _Account(), "view"
        ) is True
        # update не выдан — доступа нет.
        assert await permissions.has_account_action(
            None, identity, _Account(), "update"
        ) is False

    async def test_no_role_no_grant_denied(self, acl_stub):
        identity = _identity(roles=[])
        assert await permissions.has_account_action(
            None, identity, _Account(), "delete"
        ) is False

    async def test_console_grant(self, acl_stub):
        """Console — отдельное per-account право, без view_password."""
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"console"})
        assert await permissions.has_account_action(
            None, identity, _Account(), "console"
        ) is True
        assert await permissions.has_account_action(
            None, identity, _Account(), "view_password"
        ) is False

    async def test_provision_deprovision_grant(self, acl_stub):
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"provision"})
        assert await permissions.has_account_action(
            None, identity, _Account(), "provision"
        ) is True
        assert await permissions.has_account_action(
            None, identity, _Account(), "deprovision"
        ) is False

    async def test_department_admin_bypass_same_dept(self, acl_stub):
        identity = _identity(
            roles=[], platform_role="department_admin", department_id="dep_a"
        )
        assert await permissions.has_account_action(
            None, identity, _Account(department_id="dep_a"), "delete"
        ) is True

    async def test_department_admin_no_bypass_cross_dept(self, acl_stub):
        identity = _identity(
            roles=[], platform_role="department_admin", department_id="dep_a"
        )
        assert await permissions.has_account_action(
            None, identity, _Account(department_id="dep_b"), "delete"
        ) is False

    async def test_non_grantable_action_only_role(self, acl_stub):
        """manage_account_acl не грантуется per-account — только роль."""
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"view"})
        assert await permissions.has_account_action(
            None, identity, _Account(), "manage_account_acl"
        ) is False


class TestRequireAccountAction:
    async def test_passes_with_grant(self, acl_stub):
        identity = _identity(roles=[])
        acl_stub.grant("usr_user", {"update"})
        await permissions.require_account_action(
            None, identity, _Account(), "update"
        )

    async def test_raises_without_access(self, acl_stub):
        identity = _identity(roles=[])
        with pytest.raises(AuthorizationError) as exc:
            await permissions.require_account_action(
                None, identity, _Account(), "delete"
            )
        assert exc.value.error_code == "PERMISSION_DENIED"
        assert exc.value.details["action"] == "delete"
