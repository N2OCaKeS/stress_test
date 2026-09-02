"""Каскадные удаления и FK constraints (живая БД с per-test SAVEPOINT)."""

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models import (
    Ban, BotAccount, BotServiceRole, BotToken,
    Department, PersonalAccessToken, PlatformService, Session,
    User, UserGroup, UserGroupMembership, UserServiceRole,
)
from src.utils.ids import _new_id


# ── Department: RESTRICT на пользователей, CASCADE на группы ─────────────────

class TestDepartmentDelete:
    async def test_cannot_delete_department_with_users(self, db, user_a):
        """`User.department_id` имеет ondelete RESTRICT — попытка удалить
        непустой отдел должна вызвать IntegrityError."""
        dept_id = user_a.department_id
        with pytest.raises(IntegrityError):
            await db.execute(delete(Department).where(Department.id == dept_id))
            await db.flush()
        await db.rollback()

    async def test_deleting_department_cascades_user_groups(self, db, dept_a, admin_token):
        """`UserGroup.department_id` имеет ondelete CASCADE — удаление отдела
        каскадно удаляет связанные группы. Запускаем без user в отделе."""
        grp = UserGroup(
            id=_new_id("grp_"), department_id=dept_a.id,
            name="cascade_grp", is_active=True,
        )
        db.add(grp)
        await db.flush()
        await db.execute(delete(Department).where(Department.id == dept_a.id))
        await db.flush()
        # Группа должна исчезнуть вместе с отделом.
        remaining = (await db.execute(select(UserGroup).where(UserGroup.id == grp.id))).scalar_one_or_none()
        assert remaining is None


# ── User: cascade sessions / PAT / bans / service_roles / memberships ────────

class TestUserDeleteCascade:
    async def test_delete_user_cascades_sessions_and_pats(self, db, user_a):
        # Создадим session и PAT для user_a
        sess = Session(
            id=_new_id("ses_"), user_id=user_a.id,
            refresh_token_hash="h" * 64, expires_at=__import__("datetime").datetime(
                2030, 1, 1, tzinfo=__import__("datetime").timezone.utc,
            ),
            is_active=True,
        )
        pat = PersonalAccessToken(
            id=_new_id("pat_"), user_id=user_a.id,
            name="cascade_pat", token_hash="h" * 64, token_prefix="dbos_pat_x",
            allowed_services=[],
        )
        db.add_all([sess, pat])
        await db.flush()

        # Удалим юзера
        await db.execute(delete(User).where(User.id == user_a.id))
        await db.flush()
        # Sessions и PAT должны исчезнуть.
        assert (await db.execute(select(Session).where(Session.id == sess.id))).scalar_one_or_none() is None
        assert (
            await db.execute(select(PersonalAccessToken).where(PersonalAccessToken.id == pat.id))
        ).scalar_one_or_none() is None

    async def test_delete_user_cascades_bans_and_service_roles(self, db, user_a, dept_a_with_service, service_x):
        ban = Ban(
            id=_new_id("ban_"), user_id=user_a.id, banned_by="usr_admin",
            ban_type="permanent", reason="x", is_active=True,
        )
        usr_role = UserServiceRole(
            id=_new_id("usr_"), user_id=user_a.id,
            service_name=service_x.service_name, role="operator", is_active=True,
        )
        db.add_all([ban, usr_role])
        await db.flush()

        await db.execute(delete(User).where(User.id == user_a.id))
        await db.flush()

        assert (await db.execute(select(Ban).where(Ban.id == ban.id))).scalar_one_or_none() is None
        assert (await db.execute(select(UserServiceRole).where(UserServiceRole.id == usr_role.id))).scalar_one_or_none() is None

    async def test_delete_user_removes_group_membership(self, db, user_a, dept_a):
        grp = UserGroup(
            id=_new_id("grp_"), department_id=dept_a.id,
            name="mem_cascade", is_active=True,
        )
        db.add(grp)
        await db.flush()
        mem = UserGroupMembership(id=_new_id("gms_"), group_id=grp.id, user_id=user_a.id)
        db.add(mem)
        await db.flush()

        await db.execute(delete(User).where(User.id == user_a.id))
        await db.flush()
        assert (
            await db.execute(select(UserGroupMembership).where(UserGroupMembership.id == mem.id))
        ).scalar_one_or_none() is None


# ── Bot: cascade tokens + service_roles ──────────────────────────────────────

class TestBotDeleteCascade:
    async def test_delete_bot_cascades_tokens_and_roles(self, db, dept_a_with_service, service_x):
        bot = BotAccount(
            id=_new_id("bot_"), name="cascade_bot",
            department_id=dept_a_with_service.id,
            allowed_services=[service_x.service_name],
            status="active", is_active=True,
        )
        db.add(bot)
        await db.flush()
        tok = BotToken(
            id=_new_id("btk_"), bot_id=bot.id, name="tok",
            token_hash="h" * 64, token_prefix="dbos_bot_x",
        )
        rl = BotServiceRole(
            id=_new_id("bsr_"), bot_id=bot.id,
            service_name=service_x.service_name, role="reader", is_active=True,
        )
        db.add_all([tok, rl])
        await db.flush()

        await db.execute(delete(BotAccount).where(BotAccount.id == bot.id))
        await db.flush()
        assert (await db.execute(select(BotToken).where(BotToken.id == tok.id))).scalar_one_or_none() is None
        assert (
            await db.execute(select(BotServiceRole).where(BotServiceRole.id == rl.id))
        ).scalar_one_or_none() is None


# ── PlatformService: cascade на department_service_access и role definitions ─

class TestServiceDeleteCascade:
    async def test_delete_service_cascades_role_definitions(self, db, dept_a_with_service, service_x):
        """`ServiceRoleDefinition.service_name` ondelete CASCADE."""
        from src.models import ServiceRoleDefinition
        existing = (await db.execute(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_x.service_name
            )
        )).scalars().all()
        assert existing, "fixture must seed role definitions"
        await db.execute(
            delete(PlatformService).where(PlatformService.service_name == service_x.service_name)
        )
        await db.flush()
        leftover = (await db.execute(
            select(ServiceRoleDefinition).where(
                ServiceRoleDefinition.service_name == service_x.service_name
            )
        )).scalars().all()
        assert leftover == []
