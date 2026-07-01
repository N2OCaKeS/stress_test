"""Service-level тесты тип-wide матрицы прав secret_service.

Покрытие: grant/revoke, идемпотентность, иммутабельность системных ролей
(guest/admin → 409), валидация пары entity×action (422), department-isolation,
scope видимости в list.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.repositories import entity_permissions as repo
from src.services import permission_service

DEPT_A = "dep_a000000000000000000000000001"
DEPT_B = "dep_b000000000000000000000000001"


def _identity(
    *,
    user_id: str = "usr_admin00000000000000000000001",
    actor_type: str = "user",
    department_id: str | None = DEPT_A,
    roles: list[str] | None = None,
    platform_role: str | None = "department_admin",
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


@pytest.mark.asyncio
async def test_grant_creates_row(adb) -> None:
    actor = _identity()
    obj = await permission_service.grant_action(
        adb, actor, entity_type="secret", role="reader", action="read"
    )
    assert obj.role == "reader"
    assert obj.action == "read"
    assert obj.department_id == DEPT_A
    assert obj.id.startswith("prm_")


@pytest.mark.asyncio
async def test_grant_idempotent_same_row(adb) -> None:
    actor = _identity()
    first = await permission_service.grant_action(
        adb, actor, entity_type="secret", role="operator", action="reveal"
    )
    second = await permission_service.grant_action(
        adb, actor, entity_type="secret", role="operator", action="reveal"
    )
    assert first.id == second.id


@pytest.mark.asyncio
async def test_grant_invalid_action_422(adb) -> None:
    actor = _identity()
    with pytest.raises(DomainValidationError) as exc:
        await permission_service.grant_action(
            adb, actor, entity_type="secret", role="reader", action="power_on"
        )
    assert exc.value.error_code == "INVALID_ACTION_FOR_ENTITY"


@pytest.mark.asyncio
async def test_grant_list_guest_not_grantable_422(adb) -> None:
    """list_guest — служебная проекция, в матрицу не грантуется."""
    actor = _identity()
    with pytest.raises(DomainValidationError):
        await permission_service.grant_action(
            adb, actor, entity_type="secret", role="reader", action="list_guest"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["guest", "admin"])
async def test_grant_system_role_immutable_409(adb, role) -> None:
    actor = _identity()
    with pytest.raises(ConflictError) as exc:
        await permission_service.grant_action(
            adb, actor, entity_type="secret", role=role, action="read"
        )
    assert exc.value.error_code == "SYSTEM_ROLE_IMMUTABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["guest", "admin"])
async def test_revoke_system_role_immutable_409(adb, role) -> None:
    actor = _identity()
    with pytest.raises(ConflictError) as exc:
        await permission_service.revoke_action(
            adb, actor, entity_type="secret", role=role, action="read"
        )
    assert exc.value.error_code == "SYSTEM_ROLE_IMMUTABLE"


@pytest.mark.asyncio
async def test_revoke_missing_404(adb) -> None:
    actor = _identity()
    with pytest.raises(NotFoundError) as exc:
        await permission_service.revoke_action(
            adb, actor, entity_type="secret", role="reader", action="delete"
        )
    assert exc.value.error_code == "PERMISSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_grant_then_revoke(adb) -> None:
    actor = _identity()
    await permission_service.grant_action(
        adb, actor, entity_type="secret", role="reader", action="write"
    )
    await permission_service.revoke_action(
        adb, actor, entity_type="secret", role="reader", action="write"
    )
    assert await repo.get(adb, "secret", "reader", "write", DEPT_A) is None


@pytest.mark.asyncio
async def test_cross_dept_grant_isolation_403(adb) -> None:
    actor = _identity(department_id=DEPT_A, platform_role="department_admin")
    with pytest.raises(AuthorizationError) as exc:
        await permission_service.grant_action(
            adb, actor, entity_type="secret", role="reader", action="read",
            target_department_id=DEPT_B,
        )
    assert exc.value.error_code == "DEPARTMENT_ISOLATION"


@pytest.mark.asyncio
async def test_non_admin_denied(adb) -> None:
    actor = _identity(roles=["reader"], platform_role=None)
    with pytest.raises(AuthorizationError) as exc:
        await permission_service.grant_action(
            adb, actor, entity_type="secret", role="operator", action="read"
        )
    assert exc.value.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_service_admin_can_manage(adb) -> None:
    actor = _identity(roles=["admin"], platform_role=None)
    obj = await permission_service.grant_action(
        adb, actor, entity_type="secret", role="operator", action="read"
    )
    assert obj.department_id == DEPT_A


@pytest.mark.asyncio
async def test_account_admin_targets_any_dept(adb) -> None:
    aa = _identity(user_id="usr_acc00000000000000000000001", department_id=None, platform_role="account_admin")
    obj = await permission_service.grant_action(
        adb, aa, entity_type="secret", role="reader", action="read",
        target_department_id=DEPT_B,
    )
    assert obj.department_id == DEPT_B


@pytest.mark.asyncio
async def test_bot_cannot_manage_matrix(adb) -> None:
    bot = _identity(
        user_id="bot_x000000000000000000000000001",
        actor_type="bot",
        roles=["admin"],
        platform_role=None,
    )
    with pytest.raises(AuthorizationError) as exc:
        await permission_service.grant_action(
            adb, bot, entity_type="secret", role="operator", action="read"
        )
    assert exc.value.error_code == "DEPARTMENT_ISOLATION"


@pytest.mark.asyncio
async def test_list_scope_shows_own_dept_and_system(adb) -> None:
    actor = _identity(department_id=DEPT_A)
    await permission_service.grant_action(
        adb, actor, entity_type="secret", role="reader", action="read"
    )
    rows = await permission_service.list_all(adb, actor, role="reader")
    # Свой dept-грант виден.
    assert any(r.role == "reader" and r.department_id == DEPT_A for r in rows)


@pytest.mark.asyncio
async def test_list_all_includes_seeded_system_roles(adb) -> None:
    actor = _identity()
    guest_rows = await permission_service.list_all(adb, actor, role="guest")
    actions = {r.action for r in guest_rows}
    assert "read" in actions
    admin_rows = await permission_service.list_all(adb, actor, role="admin")
    admin_actions = {r.action for r in admin_rows}
    assert {"read", "reveal", "write", "delete", "grant_acl", "grant_dept", "manage_status"} <= admin_actions
