"""DB-level тесты для BotGroupMembership.

Проверяем:
- UniqueConstraint uq_bot_group_membership (group_id, bot_id)
- CASCADE DELETE при удалении группы → memberships удаляются
- CASCADE DELETE при удалении бота → memberships удаляются
- added_at устанавливается автоматически (server_default=now())
- added_by может быть NULL
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.models import BotAccount, Department
from src.models.bot_group_membership import BotGroupMembership
from src.models.user_group import UserGroup
from src.utils.ids import _new_id


async def _make_dept(db, name):
    dept = Department(
        id=_new_id("dep_"), name=name, is_active=True,
    )
    db.add(dept)
    await db.flush()
    return dept


async def _make_bot(db, dept_id, name):
    bot = BotAccount(
        id=_new_id("bot_"), name=name, department_id=dept_id,
        allowed_services=[], is_active=True,
    )
    db.add(bot)
    await db.flush()
    return bot


async def _make_group(db, dept_id, name):
    grp = UserGroup(
        id=_new_id("grp_"), department_id=dept_id,
        name=name, is_active=True,
    )
    db.add(grp)
    await db.flush()
    return grp


async def _add_bot_to_group(db, group_id, bot_id, added_by=None):
    m = BotGroupMembership(
        id=_new_id("bgm_"),
        group_id=group_id,
        bot_id=bot_id,
        added_by=added_by,
    )
    db.add(m)
    await db.flush()
    return m


class TestBotGroupMembershipConstraints:
    async def test_unique_constraint_prevents_duplicate(self, db):
        """(group_id, bot_id) уникален — второй INSERT должен дать IntegrityError."""
        dept = await _make_dept(db, "bgm_dept")
        bot = await _make_bot(db, dept.id, "bgm_bot_dup")
        grp = await _make_group(db, dept.id, "bgm_grp_dup")

        await _add_bot_to_group(db, grp.id, bot.id)

        with pytest.raises(IntegrityError):
            await _add_bot_to_group(db, grp.id, bot.id)

    async def test_different_bots_same_group_allowed(self, db):
        """Два разных бота в одной группе — не нарушает unique constraint."""
        dept = await _make_dept(db, "bgm_dept2")
        bot1 = await _make_bot(db, dept.id, "bgm_bot_a")
        bot2 = await _make_bot(db, dept.id, "bgm_bot_b")
        grp = await _make_group(db, dept.id, "bgm_grp2")

        m1 = await _add_bot_to_group(db, grp.id, bot1.id)
        m2 = await _add_bot_to_group(db, grp.id, bot2.id)
        assert m1.id != m2.id

    async def test_same_bot_different_groups_allowed(self, db):
        """Один бот в двух группах — допустимо."""
        dept = await _make_dept(db, "bgm_dept3")
        bot = await _make_bot(db, dept.id, "bgm_bot_multi")
        grp1 = await _make_group(db, dept.id, "bgm_grp3a")
        grp2 = await _make_group(db, dept.id, "bgm_grp3b")

        m1 = await _add_bot_to_group(db, grp1.id, bot.id)
        m2 = await _add_bot_to_group(db, grp2.id, bot.id)
        assert m1.id != m2.id

    async def test_added_by_nullable(self, db):
        """added_by=NULL — разрешён схемой (nullable=True)."""
        dept = await _make_dept(db, "bgm_dept4")
        bot = await _make_bot(db, dept.id, "bgm_bot_null")
        grp = await _make_group(db, dept.id, "bgm_grp4")

        m = await _add_bot_to_group(db, grp.id, bot.id, added_by=None)
        assert m.added_by is None

    async def test_added_at_set_automatically(self, db):
        """server_default=now() — added_at заполняется после flush."""
        dept = await _make_dept(db, "bgm_dept5")
        bot = await _make_bot(db, dept.id, "bgm_bot_ts")
        grp = await _make_group(db, dept.id, "bgm_grp5")

        m = await _add_bot_to_group(db, grp.id, bot.id)
        # После flush server_default применяется, но ORM-объект может не иметь
        # значения до refresh. Проверим что не None после refresh.
        await db.refresh(m)
        assert m.added_at is not None

    async def test_cascade_delete_on_group_removal(self, db):
        """DELETE group → membership CASCADE удаляется."""
        dept = await _make_dept(db, "bgm_dept6")
        bot = await _make_bot(db, dept.id, "bgm_bot_casc")
        grp = await _make_group(db, dept.id, "bgm_grp_casc")

        m = await _add_bot_to_group(db, grp.id, bot.id)
        membership_id = m.id

        await db.delete(grp)
        await db.flush()

        result = await db.scalar(
            select(BotGroupMembership).where(BotGroupMembership.id == membership_id)
        )
        assert result is None, "membership must be CASCADE deleted when group is deleted"

    async def test_cascade_delete_on_bot_removal(self, db):
        """DELETE bot → membership CASCADE удаляется."""
        dept = await _make_dept(db, "bgm_dept7")
        bot = await _make_bot(db, dept.id, "bgm_bot_casc2")
        grp = await _make_group(db, dept.id, "bgm_grp_casc2")

        m = await _add_bot_to_group(db, grp.id, bot.id)
        membership_id = m.id

        await db.delete(bot)
        await db.flush()

        result = await db.scalar(
            select(BotGroupMembership).where(BotGroupMembership.id == membership_id)
        )
        assert result is None, "membership must be CASCADE deleted when bot is deleted"
