"""update_bot — две защитные проверки:

* После update_bot identity-кэш бота сбрасывается — иначе allowed_services /
  is_active / status / name из старой версии живут в introspect до истечения
  TTL (~5 секунд). По CLAUDE-инварианту «отзыв доступа вступает в силу
  немедленно» — это нарушение.
* Cross-tenant guard не должен пускать DEPARTMENT_ADMIN'а с
  `actor_department_id=None` (data integrity bug) — должен честно отдать
  403 BOT_UPDATE_FORBIDDEN, как все остальные bot-функции через
  `_check_can_manage_bot_or_audit`.
"""

import pytest

from src.core.constants import PlatformRole
from src.schemas.bots import BotCreate, BotUpdate
from src.services import bot_service

BOTS_URL = "/api/auth/v1/bots"


# ── invalidate_identity_cache спай ────────────────────────────────────────────


@pytest.fixture()
def spy_invalidate(monkeypatch):
    seen: list[str] = []

    def spy(subject_id: str) -> None:
        seen.append(subject_id)

    monkeypatch.setattr(bot_service, "_invalidate_identity_cache", spy)
    return seen


# ── #1: update_bot invalidates identity cache ─────────────────────────────────


async def test_update_bot_invalidates_identity_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Сужение allowed_services должно сбросить identity-кэш бота."""
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="cache_upd_bot",
            department_id=dept_a_with_service.id,
            allowed_services=[service_x.service_name],
        ),
    )
    spy_invalidate.clear()

    await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=created.bot_id,
        data=BotUpdate(allowed_services=[]),
    )

    assert created.bot_id in spy_invalidate, (
        "update_bot должен инвалидировать identity-кэш — иначе introspect "
        "вернёт старые allowed_services до истечения TTL"
    )


async def test_update_bot_status_change_invalidates_cache(
    db, account_admin, dept_a, spy_invalidate,
):
    """Status active→disabled тоже должен сбрасывать кэш."""
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="cache_status_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )
    spy_invalidate.clear()

    await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=created.bot_id,
        data=BotUpdate(status="disabled"),
    )

    assert created.bot_id in spy_invalidate


# ── #2: DEPARTMENT_ADMIN с actor_department_id=None — 403, не bypass ──────────


async def test_update_bot_dept_admin_with_none_dept_is_forbidden(
    db, dept_b, account_admin,
):
    """Broken identity (DEPARTMENT_ADMIN, dept=None) → 403 BOT_UPDATE_FORBIDDEN.

    До фикса `if actor_dept_id is not None and ...` тихо пропускал такого
    актора через guard, и он мог патчить ботов чужих отделов.
    """
    # Бот в dept_b создаётся account_admin'ом.
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="none_dept_target_bot",
            department_id=dept_b.id,
            allowed_services=[],
        ),
    )

    # actor_department_id=None имитирует broken IdentityContext.
    # Чтобы фолбэк-SELECT в _resolve_actor_dept тоже не дал валидный dept_a,
    # подменим dept_admin_a.id на актор без записи в users.
    from src.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError) as exc:
        await bot_service.update_bot(
            db,
            actor_id="usr_does_not_exist_in_db",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            bot_id=bot.bot_id,
            data=BotUpdate(description="hijack"),
            actor_department_id=None,
        )
    assert exc.value.error_code == "BOT_UPDATE_FORBIDDEN"


async def test_update_bot_account_admin_with_no_dept_still_allowed(
    db, account_admin, dept_a,
):
    """ACCOUNT_ADMIN без dept (штатно) — guard не запускается, update проходит.

    Регрессионный страх: после удаления `is not None` не сломалось ли
    случайно поведение для платформенных админов (у них dept_id всегда None).
    """
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="acct_admin_upd_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )

    updated = await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=bot.bot_id,
        data=BotUpdate(description="ok"),
        actor_department_id=None,
    )
    assert updated.description == "ok"
