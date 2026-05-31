"""create_bot vs broken identity + BotStatus enum/PATCH alignment.

* `create_bot` теперь симметричен `update_bot`: DEPARTMENT_ADMIN с
  `actor_department_id=None` (broken identity) проваливается в 403
  BOT_CREATION_FORBIDDEN, а не проскакивает guard.
* `BotStatus.BLOCKED` имеет wire-value `"disabled"` — совпадает с
  `BotUpdate.status` и с тем, что реально кладётся в БД при PATCH.
  `BotStatus(value)` теперь не падает на строке из API.
"""

import pytest
from sqlalchemy import select

from src.core.constants import BotStatus, PlatformRole
from src.core.exceptions import AuthorizationError
from src.models import BotAccount
from src.schemas.bots import BotCreate, BotUpdate
from src.services import bot_service


# ── create_bot: broken identity DEPARTMENT_ADMIN → 403 ────────────────────────


async def test_create_bot_dept_admin_with_none_dept_is_forbidden(
    db, dept_b,
):
    """DEPARTMENT_ADMIN c actor_department_id=None пытается создать бота
    в чужом отделе → 403 BOT_CREATION_FORBIDDEN (без `is not None`-bypass).
    """
    with pytest.raises(AuthorizationError) as exc:
        await bot_service.create_bot(
            db,
            actor_id="usr_does_not_exist_in_db",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            data=BotCreate(
                name="bot_none_dept_create",
                department_id=dept_b.id,
                allowed_services=[],
            ),
            actor_department_id=None,
        )
    assert exc.value.error_code == "BOT_CREATION_FORBIDDEN"


async def test_create_bot_account_admin_with_none_dept_still_allowed(
    db, account_admin, dept_a,
):
    """ACCOUNT_ADMIN без dept (штатно) — guard не запускается, create проходит.
    Регрессия на случай, если убрали ветку через `is not None`.
    """
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="acct_admin_create_ok",
            department_id=dept_a.id,
            allowed_services=[],
        ),
        actor_department_id=None,
    )
    assert created.department_id == dept_a.id


# ── BotStatus enum / wire-value sync ─────────────────────────────────────────


def test_bot_status_blocked_wire_value_is_disabled():
    """Wire-value enum'а синхронен с `BotUpdate.status` Literal-ом."""
    assert BotStatus.BLOCKED.value == "disabled"
    # Lookup через значение не падает — это и есть основной инвариант.
    assert BotStatus("disabled") is BotStatus.BLOCKED
    assert BotStatus("active") is BotStatus.ACTIVE


async def test_update_bot_status_disabled_persists_enum_value(
    db, account_admin, dept_a,
):
    """PATCH status="disabled" пишет в БД значение, валидное для BotStatus()."""
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="status_enum_sync_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )

    await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=created.bot_id,
        data=BotUpdate(status="disabled"),
    )

    row = await db.scalar(select(BotAccount).where(BotAccount.id == created.bot_id))
    await db.refresh(row)
    assert row.status == BotStatus.BLOCKED.value == "disabled"
    assert row.is_active is False
    # Не падает: значение из БД полностью валидно для enum'а.
    assert BotStatus(row.status) is BotStatus.BLOCKED
