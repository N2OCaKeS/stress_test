"""Read-side инстанс-ACL — прямые проверки на сессии БД, без HTTP.

Покрывает функции `services/permissions`, отвечающие за точечные гранты роли
на конкретный ресурс:

* `has_resource_action` — инстанс-грант даёт action на ЭТОМ ресурсе и не течёт
  на другие того же типа; union с тип-wide матрицей; dept-scope матча.
* `effective_resource_actions` — union тип-wide и инстанс-действий.
* `has_resource_grant` / `visible_resource_ids` / `granted_resource_ids` —
  расширение видимости по инстанс-грантам.
* `has_account_action` — инстанс-грант на учётку + department_admin bypass.
"""

from __future__ import annotations

import pytest

from src.core.constants import Action, EntityType
from src.core.exceptions import NotFoundError
from src.repositories import entity_permission as ep_repo
from src.repositories import resource_role_permission as rrp_repo
from src.schemas.identity import IdentityContext
from src.services import permissions
from src.services.server import load_visible_server
from src.utils.ids import entity_permission_id, resource_role_permission_id


def _identity(
    *, roles, department_id="dep_a", platform_role=None, subject_type=None,
):
    return IdentityContext(
        user_id="usr_unit",
        username="unit",
        department_id=department_id,
        allowed_services=["server_service"],
        service_roles={"server_service": list(roles)} if roles else {},
        platform_role=platform_role,
        subject_type=subject_type,
    )


async def _grant_instance(
    db, *, resource_type, resource_id, role, action, department_id="dep_a",
):
    await rrp_repo.grant(
        db,
        permission_id=resource_role_permission_id(),
        resource_type=resource_type,
        resource_id=resource_id,
        role=role,
        action=action,
        granted_by=None,
        department_id=department_id,
    )
    await db.flush()


async def _grant_typewide(db, *, entity_type, role, action, department_id=None):
    await ep_repo.grant(
        db,
        permission_id=entity_permission_id(),
        entity_type=entity_type,
        role=role,
        action=action,
        granted_by=None,
        department_id=department_id,
    )
    await db.flush()


class TestHasResourceAction:
    async def test_instance_grant_is_resource_scoped(self, db, make_server):
        """Грант на srv1 даёт action только на srv1, не на srv2 того же типа."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv1.id,
            role="limited", action=Action.POWER_STATUS,
        )
        identity = _identity(roles=["limited"])

        assert await permissions.has_resource_action(
            db, identity, EntityType.SERVER, srv1.id, Action.POWER_STATUS
        )
        assert not await permissions.has_resource_action(
            db, identity, EntityType.SERVER, srv2.id, Action.POWER_STATUS
        )

    async def test_typewide_grant_covers_all_without_instance(self, db, make_server):
        """Тип-wide грант действует и без инстанс-строки — union включает матрицу."""
        srv = await make_server(department_id="dep_a")
        await _grant_typewide(
            db, entity_type=EntityType.SERVER, role="limited",
            action=Action.POWER_STATUS, department_id="dep_a",
        )
        identity = _identity(roles=["limited"])
        assert await permissions.has_resource_action(
            db, identity, EntityType.SERVER, srv.id, Action.POWER_STATUS
        )

    async def test_instance_grant_dept_scope_mismatch(self, db, make_server):
        """Per-dept инстанс-грант (dep_a) не матчится caller'у из dep_b."""
        srv = await make_server(department_id="dep_a")
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv.id,
            role="limited", action=Action.POWER_STATUS, department_id="dep_a",
        )
        other_dept = _identity(roles=["limited"], department_id="dep_b")
        assert not await permissions.has_resource_action(
            db, other_dept, EntityType.SERVER, srv.id, Action.POWER_STATUS
        )

    async def test_no_roles_is_false(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        identity = _identity(roles=[])
        assert not await permissions.has_resource_action(
            db, identity, EntityType.SERVER, srv.id, Action.POWER_STATUS
        )


class TestEffectiveResourceActions:
    async def test_union_typewide_and_instance(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        await _grant_typewide(
            db, entity_type=EntityType.SERVER, role="limited",
            action=Action.VIEW, department_id="dep_a",
        )
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv.id,
            role="limited", action=Action.POWER_ON,
        )
        identity = _identity(roles=["limited"])
        actions = await permissions.effective_resource_actions(
            db, identity, EntityType.SERVER, srv.id
        )
        assert Action.VIEW in actions
        assert Action.POWER_ON in actions


class TestVisibilityHelpers:
    async def test_has_resource_grant_true_false(self, db, make_server):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv1.id,
            role="limited", action=Action.VIEW,
        )
        identity = _identity(roles=["limited"])
        assert await permissions.has_resource_grant(
            db, identity, EntityType.SERVER, srv1.id
        )
        assert not await permissions.has_resource_grant(
            db, identity, EntityType.SERVER, srv2.id
        )

    async def test_visible_resource_ids_subset(self, db, make_server):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        for s in (srv1, srv2):
            await _grant_instance(
                db, resource_type=EntityType.SERVER, resource_id=s.id,
                role="limited", action=Action.VIEW,
            )
        identity = _identity(roles=["limited"])
        visible = await permissions.visible_resource_ids(
            db, identity, EntityType.SERVER, [srv1.id, srv2.id, srv3.id]
        )
        assert visible == {srv1.id, srv2.id}

    async def test_granted_resource_ids_returns_all_granted(self, db, make_server):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv1.id,
            role="limited", action=Action.VIEW,
        )
        await _grant_instance(
            db, resource_type=EntityType.SERVER, resource_id=srv2.id,
            role="limited", action=Action.POWER_STATUS,
        )
        identity = _identity(roles=["limited"])
        granted = await permissions.granted_resource_ids(
            db, identity, EntityType.SERVER
        )
        assert granted == {srv1.id, srv2.id}


class TestHasAccountAction:
    async def test_instance_grant_on_account(self, db, make_server, make_account):
        srv = await make_server(department_id="dep_a")
        acc1 = await make_account(server_id=srv.id, login="svc1")
        acc2 = await make_account(server_id=srv.id, login="svc2")
        await _grant_instance(
            db, resource_type=EntityType.SERVER_ACCOUNT, resource_id=acc1.id,
            role="limited", action=Action.VIEW_PASSWORD,
        )
        identity = _identity(roles=["limited"])
        assert await permissions.has_account_action(
            db, identity, acc1, Action.VIEW_PASSWORD
        )
        # Та же роль без инстанс-гранта на acc2 — нет доступа.
        assert not await permissions.has_account_action(
            db, identity, acc2, Action.VIEW_PASSWORD
        )

    async def test_department_admin_bypass_same_dept(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc")
        dep_admin = _identity(
            roles=[], department_id="dep_a", platform_role="department_admin",
        )
        assert await permissions.has_account_action(
            db, dep_admin, acc, Action.VIEW_PASSWORD
        )

    async def test_department_admin_other_dept_no_bypass(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc")
        dep_admin_b = _identity(
            roles=[], department_id="dep_b", platform_role="department_admin",
        )
        assert not await permissions.has_account_action(
            db, dep_admin_b, acc, Action.VIEW_PASSWORD
        )


class TestServerVisibleViaAccountGrant:
    """Неявная видимость родительского сервера по инстанс-гранту на учётку.

    Сценарий — caller из чужого отдела с system-wide грантом на учётку: сама
    учётка ему видима (cross-dept path в `_load_account_visible`), а
    `load_visible_server` без этой ветки прятал бы родительский сервер за 404,
    хотя дойти до учётки без него нельзя.
    """

    async def test_cross_dept_account_grant_makes_server_visible(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc")
        await _grant_instance(
            db, resource_type=EntityType.SERVER_ACCOUNT, resource_id=acc.id,
            role="limited", action=Action.VIEW, department_id=None,
        )
        identity = _identity(roles=["limited"], department_id="dep_b")
        loaded = await load_visible_server(db, identity, srv.id)
        assert loaded.id == srv.id

    async def test_cross_dept_without_account_grant_404(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="svc")
        identity = _identity(roles=["limited"], department_id="dep_b")
        with pytest.raises(NotFoundError):
            await load_visible_server(db, identity, srv.id)

    async def test_account_grant_does_not_grant_server_action(
        self, db, make_server, make_account,
    ):
        """Видимость для навигации не даёт тип-wide прав на сам сервер."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc")
        await _grant_instance(
            db, resource_type=EntityType.SERVER_ACCOUNT, resource_id=acc.id,
            role="limited", action=Action.VIEW, department_id=None,
        )
        identity = _identity(roles=["limited"], department_id="dep_b")
        assert not await permissions.has_resource_action(
            db, identity, EntityType.SERVER, srv.id, Action.VIEW
        )
