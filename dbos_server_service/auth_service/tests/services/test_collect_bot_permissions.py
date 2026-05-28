"""Unit-тесты для auth_service.collect_bot_permissions и его ветвлений.

Проверяем через реальную БД (как все остальные сервисные тесты):
- бот с allowed_services ∩ dept_services = effective_services
- direct_roles для сервисов не в effective → отбрасываются
- group_roles для сервисов не в effective → отбрасываются
- group_roles + direct_roles для одного сервиса → merge без дублей
- бот без allowed_services → effective_services пустой, service_roles пустой
- dept без активных сервисов → effective_services пустой
"""

import pytest

from src.services.auth_service import collect_bot_permissions
from tests.conftest import (
    _make_dept,
    _make_service,
    _grant_service,
    _make_user,
    _make_role_def,
)
from src.models import BotAccount, BotGroupMembership
from src.utils.ids import _new_id


async def _make_bot(db, dept_id, name, allowed_services=None):
    bot = BotAccount(
        id=_new_id("bot_"),
        name=name,
        department_id=dept_id,
        allowed_services=allowed_services or [],
        is_active=True,
    )
    db.add(bot)
    await db.flush()
    return bot


class TestCollectBotPermissions:
    async def test_bot_effective_services_intersect_dept(
        self, db, dept_a_with_service, service_x,
    ):
        """allowed_services=[service_x] при dept с service_x → effective=[service_x]."""
        bot = await _make_bot(
            db, dept_a_with_service.id, "bp_bot1",
            allowed_services=[service_x.service_name],
        )
        effective, roles = await collect_bot_permissions(db, bot)
        assert service_x.service_name in effective
        assert isinstance(roles, dict)

    async def test_bot_allowed_service_not_in_dept_excluded(
        self, db, dept_a_with_service, service_x,
    ):
        """bot.allowed_services содержит ghost_svc, которого нет в dept → excluded."""
        bot = await _make_bot(
            db, dept_a_with_service.id, "bp_bot2",
            allowed_services=["ghost_service"],
        )
        effective, roles = await collect_bot_permissions(db, bot)
        assert "ghost_service" not in effective
        assert roles == {}

    async def test_bot_with_empty_allowed_services(
        self, db, dept_a_with_service,
    ):
        """allowed_services=[] → effective пустой."""
        bot = await _make_bot(db, dept_a_with_service.id, "bp_bot3", allowed_services=[])
        effective, roles = await collect_bot_permissions(db, bot)
        assert effective == []
        assert roles == {}

    async def test_bot_direct_role_outside_effective_dropped(
        self, db, dept_a_with_service, service_x,
    ):
        """Прямая роль бота для сервиса, который НЕ в bot.allowed_services — отбрасывается.

        Симулируем stale-строку: создаём второй сервис (service_y) с доступом у dept_a,
        но bot.allowed_services содержит только service_x → service_y вне effective.
        Прямая BotServiceRole на service_y должна отбрасываться collect_bot_permissions.
        """
        from src.models.bot_service_role import BotServiceRole

        # Второй реальный сервис с доступом dept_a — нужен для FK
        service_y = await _make_service(db, "stale_svc_y")
        await _grant_service(db, dept_a_with_service.id, service_y.service_name)

        # Бот имеет только service_x в allowed_services, не service_y
        bot = await _make_bot(
            db, dept_a_with_service.id, "bp_bot4",
            allowed_services=[service_x.service_name],
        )
        # Stale роль на service_y — dept её выдал, но бот её не включил в allowed_services
        stale = BotServiceRole(
            id=_new_id("bsr_"),
            bot_id=bot.id,
            service_name=service_y.service_name,
            role="reader",
            is_active=True,
        )
        db.add(stale)
        await db.flush()

        effective, roles = await collect_bot_permissions(db, bot)
        # service_y есть у dept, но не в allowed_services → excluded
        assert service_y.service_name not in effective
        assert service_y.service_name not in roles
        assert service_x.service_name in effective

    async def test_bot_group_role_for_allowed_service_appears(
        self, db, dept_a_with_service, service_x,
    ):
        """Бот в группе с service_roles для service_x → роль появляется в effective."""
        from src.models.user_group import UserGroup
        from src.models.group_service_access import GroupServiceAccess
        from src.models.group_service_role import GroupServiceRole

        bot = await _make_bot(
            db, dept_a_with_service.id, "bp_bot5",
            allowed_services=[service_x.service_name],
        )

        grp = UserGroup(
            id=_new_id("grp_"),
            department_id=dept_a_with_service.id,
            name="bp_grp",
            display_name="BP Group",
            is_active=True,
        )
        db.add(grp)
        await db.flush()

        access = GroupServiceAccess(
            id=_new_id("gsa_"),
            group_id=grp.id,
            service_name=service_x.service_name,
            is_active=True,
            granted_by=None,
        )
        db.add(access)
        await db.flush()

        role_row = GroupServiceRole(
            id=_new_id("gsr_"),
            group_id=grp.id,
            service_name=service_x.service_name,
            role="operator",
            assigned_by=None,
        )
        db.add(role_row)
        await db.flush()

        membership = BotGroupMembership(
            id=_new_id("bgm_"),
            group_id=grp.id,
            bot_id=bot.id,
            added_by=None,
        )
        db.add(membership)
        await db.flush()

        effective, roles = await collect_bot_permissions(db, bot)
        assert service_x.service_name in effective
        assert "operator" in roles.get(service_x.service_name, [])

    async def test_bot_group_role_outside_allowed_services_excluded(
        self, db, dept_a_with_service, service_x,
    ):
        """Группа имеет role для service_x, но бот не имеет service_x в allowed_services → excluded."""
        from src.models.user_group import UserGroup
        from src.models.group_service_access import GroupServiceAccess
        from src.models.group_service_role import GroupServiceRole

        # bot.allowed_services=[] — effective_services пустой
        bot = await _make_bot(db, dept_a_with_service.id, "bp_bot6", allowed_services=[])

        grp = UserGroup(
            id=_new_id("grp_"),
            department_id=dept_a_with_service.id,
            name="bp_grp2",
            display_name="BP Group 2",
            is_active=True,
        )
        db.add(grp)
        await db.flush()

        access = GroupServiceAccess(
            id=_new_id("gsa_"),
            group_id=grp.id,
            service_name=service_x.service_name,
            is_active=True,
            granted_by=None,
        )
        db.add(access)
        await db.flush()

        role_row = GroupServiceRole(
            id=_new_id("gsr_"),
            group_id=grp.id,
            service_name=service_x.service_name,
            role="reader",
            assigned_by=None,
        )
        db.add(role_row)
        await db.flush()

        membership = BotGroupMembership(
            id=_new_id("bgm_"),
            group_id=grp.id,
            bot_id=bot.id,
            added_by=None,
        )
        db.add(membership)
        await db.flush()

        effective, roles = await collect_bot_permissions(db, bot)
        assert effective == []
        assert roles == {}

    async def test_bot_direct_and_group_roles_merged_no_dupes(
        self, db, dept_a_with_service, service_x,
    ):
        """Прямая роль + группа с той же ролью → в merged без дублей (set)."""
        from src.models.bot_service_role import BotServiceRole
        from src.models.user_group import UserGroup
        from src.models.group_service_access import GroupServiceAccess
        from src.models.group_service_role import GroupServiceRole

        bot = await _make_bot(
            db, dept_a_with_service.id, "bp_bot7",
            allowed_services=[service_x.service_name],
        )

        # Прямая роль "reader"
        direct = BotServiceRole(
            id=_new_id("bsr_"),
            bot_id=bot.id,
            service_name=service_x.service_name,
            role="reader",
            is_active=True,
        )
        db.add(direct)
        await db.flush()

        # Группа с ролями ["reader", "operator"]
        grp = UserGroup(
            id=_new_id("grp_"),
            department_id=dept_a_with_service.id,
            name="bp_grp3",
            display_name="BP Group 3",
            is_active=True,
        )
        db.add(grp)
        await db.flush()

        access = GroupServiceAccess(
            id=_new_id("gsa_"),
            group_id=grp.id,
            service_name=service_x.service_name,
            is_active=True,
            granted_by=None,
        )
        db.add(access)
        await db.flush()

        for role_name in ["reader", "operator"]:
            role_row = GroupServiceRole(
                id=_new_id("gsr_"),
                group_id=grp.id,
                service_name=service_x.service_name,
                role=role_name,
                assigned_by=None,
            )
            db.add(role_row)
        await db.flush()

        membership = BotGroupMembership(
            id=_new_id("bgm_"),
            group_id=grp.id,
            bot_id=bot.id,
            added_by=None,
        )
        db.add(membership)
        await db.flush()

        effective, roles = await collect_bot_permissions(db, bot)
        svc_roles = roles.get(service_x.service_name, [])
        # reader + operator, без дублей
        assert set(svc_roles) == {"reader", "operator"}
        assert len(svc_roles) == len(set(svc_roles))
