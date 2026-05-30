"""Тесты: identity-cache invalidation для ботов после privilege-changing операций.

Раньше сброс кэша происходил только в `service_role_service.delete_role`.
Остальные точки (revoke dept-service access, delete platform service,
bot membership add/remove в группу, group-service grant/revoke,
group-roles assign/revoke) ботов не трогали — до истечения TTL бот
продолжал видеть в introspect снятые/добавленные права.

Тесты вызывают сервис-слой напрямую и проверяют, что после операции
`_invalidate_identity_cache` дёрнут с правильными bot_id.
"""

import pytest

from src.models import BotAccount
from src.models.bot_group_membership import BotGroupMembership
from src.models.bot_service_role import BotServiceRole
from src.models.user_group import UserGroup
from src.schemas.auth import IdentityContext
from src.services import (
    department_service,
    group_service,
    platform_service_service,
)
from src.utils.ids import (
    _new_id,
    bot_id as gen_bot_id,
    bot_service_role_id,
    group_id as gen_group_id,
)


@pytest.fixture(autouse=True)
def spy_invalidate(monkeypatch):
    """Перехватываем общий `_cache_invalidation.invalidate_identity_cache`.

    Сервисы импортируют `_invalidate_identity_cache` алиасом, но привязка
    к module-level имени — `is` тождество с
    `_cache_invalidation.invalidate_identity_cache`. Подменяем атрибут в
    каждом сервисе, чтобы перехватить вызовы независимо от того, как именно
    они зовут хелпер.
    """
    seen: list[str] = []

    def spy(subject_id: str) -> None:
        seen.append(subject_id)

    from src.services import (
        _cache_invalidation,
        department_service as dept_mod,
        group_service as grp_mod,
        platform_service_service as plat_mod,
    )

    monkeypatch.setattr(_cache_invalidation, "invalidate_identity_cache", spy)
    monkeypatch.setattr(dept_mod, "_invalidate_identity_cache", spy)
    monkeypatch.setattr(grp_mod, "_invalidate_identity_cache", spy)
    monkeypatch.setattr(plat_mod, "_invalidate_identity_cache", spy)
    return seen


def _account_admin_identity(actor_id: str) -> IdentityContext:
    return IdentityContext(
        user_id=actor_id,
        username="admin",
        department_id=None,
        platform_role="account_admin",
    )


async def _make_bot(db, dept_id: str, name: str) -> BotAccount:
    bot = BotAccount(
        id=gen_bot_id(),
        name=name,
        department_id=dept_id,
        allowed_services=[],
        is_active=True,
    )
    db.add(bot)
    await db.flush()
    return bot


async def _make_group(db, dept_id: str, name: str) -> UserGroup:
    grp = UserGroup(
        id=gen_group_id(),
        department_id=dept_id,
        name=name,
        display_name=name,
        is_active=True,
    )
    db.add(grp)
    await db.flush()
    return grp


async def _assign_bot_role(db, bot_id_: str, service_name: str, role: str) -> None:
    db.add(
        BotServiceRole(
            id=bot_service_role_id(),
            bot_id=bot_id_,
            service_name=service_name,
            role=role,
            is_active=True,
        )
    )
    await db.flush()


async def _add_bot_to_group(db, group_id_: str, bot_id_: str) -> None:
    db.add(
        BotGroupMembership(
            id=_new_id("bgm_"),
            group_id=group_id_,
            bot_id=bot_id_,
            added_by=None,
        )
    )
    await db.flush()


# ── department_service.revoke_service_access ──────────────────────────────────


async def test_revoke_service_access_invalidates_bot_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Бот с прямой ролью на (dept, service) → сброс кэша после revoke."""
    bot = await _make_bot(db, dept_a_with_service.id, "ds_bot")
    await _assign_bot_role(db, bot.id, service_x.service_name, "reader")
    await db.commit()
    spy_invalidate.clear()

    await department_service.revoke_service_access(
        db,
        actor_id=account_admin.id,
        department_id=dept_a_with_service.id,
        service_name=service_x.service_name,
    )

    assert bot.id in spy_invalidate


# ── platform_service_service.delete_service ───────────────────────────────────


async def test_delete_service_invalidates_bot_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Снос сервиса → сброс кэша у всех ботов с ролью на нём."""
    bot = await _make_bot(db, dept_a_with_service.id, "del_svc_bot")
    await _assign_bot_role(db, bot.id, service_x.service_name, "reader")
    await db.commit()
    spy_invalidate.clear()

    await platform_service_service.delete_service(
        db, actor_id=account_admin.id, service_name=service_x.service_name,
    )

    assert bot.id in spy_invalidate


# ── group_service.add_bot_member ──────────────────────────────────────────────


async def test_add_bot_member_invalidates_bot_cache(
    db, account_admin, dept_a, spy_invalidate,
):
    """Добавление бота в группу → сброс кэша этого бота."""
    grp = await _make_group(db, dept_a.id, "addmem_grp")
    bot = await _make_bot(db, dept_a.id, "addmem_bot")
    await db.commit()
    spy_invalidate.clear()

    await group_service.add_bot_member(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        bot_id=bot.id,
    )

    assert bot.id in spy_invalidate


# ── group_service.remove_bot_member ───────────────────────────────────────────


async def test_remove_bot_member_invalidates_bot_cache(
    db, account_admin, dept_a, spy_invalidate,
):
    """Удаление бота из группы → сброс кэша этого бота."""
    grp = await _make_group(db, dept_a.id, "rmmem_grp")
    bot = await _make_bot(db, dept_a.id, "rmmem_bot")
    await _add_bot_to_group(db, grp.id, bot.id)
    await db.commit()
    spy_invalidate.clear()

    await group_service.remove_bot_member(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        bot_id=bot.id,
    )

    assert bot.id in spy_invalidate


# ── group_service.grant_service_to_group ──────────────────────────────────────


async def test_grant_service_to_group_invalidates_bot_member_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Бот-мембер группы → сброс кэша после grant service группе."""
    grp = await _make_group(db, dept_a_with_service.id, "gs_grant_grp")
    bot = await _make_bot(db, dept_a_with_service.id, "gs_grant_bot")
    await _add_bot_to_group(db, grp.id, bot.id)
    await db.commit()
    spy_invalidate.clear()

    await group_service.grant_service_to_group(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )

    assert bot.id in spy_invalidate


# ── group_service.revoke_service_from_group ───────────────────────────────────


async def test_revoke_service_from_group_invalidates_bot_member_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Бот-мембер группы → сброс кэша после revoke service у группы."""
    grp = await _make_group(db, dept_a_with_service.id, "gs_revoke_grp")
    bot = await _make_bot(db, dept_a_with_service.id, "gs_revoke_bot")
    await _add_bot_to_group(db, grp.id, bot.id)
    # Сначала grant — иначе revoke не пройдёт guard.
    await group_service.grant_service_to_group(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )
    spy_invalidate.clear()

    await group_service.revoke_service_from_group(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )

    assert bot.id in spy_invalidate


# ── group_service.assign_group_roles ──────────────────────────────────────────


async def test_assign_group_roles_invalidates_bot_member_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Бот-мембер группы → сброс кэша после assign group roles."""
    grp = await _make_group(db, dept_a_with_service.id, "gr_assign_grp")
    bot = await _make_bot(db, dept_a_with_service.id, "gr_assign_bot")
    await _add_bot_to_group(db, grp.id, bot.id)
    await group_service.grant_service_to_group(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )
    spy_invalidate.clear()

    await group_service.assign_group_roles(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
        roles=["reader"],
    )

    assert bot.id in spy_invalidate


# ── group_service.revoke_group_roles ──────────────────────────────────────────


async def test_revoke_group_roles_invalidates_bot_member_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Бот-мембер группы → сброс кэша после revoke group roles."""
    grp = await _make_group(db, dept_a_with_service.id, "gr_revoke_grp")
    bot = await _make_bot(db, dept_a_with_service.id, "gr_revoke_bot")
    await _add_bot_to_group(db, grp.id, bot.id)
    await group_service.grant_service_to_group(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )
    await group_service.assign_group_roles(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
        roles=["reader"],
    )
    spy_invalidate.clear()

    await group_service.revoke_group_roles(
        db,
        identity=_account_admin_identity(account_admin.id),
        group_id=grp.id,
        service_name=service_x.service_name,
    )

    assert bot.id in spy_invalidate
