"""Use cases для настраиваемой парольной политики — платформенный singleton.

Одна строка (`SINGLETON_ID`) на всю платформу. Чтение отдаёт текущий конфиг, а
при отсутствии строки — дефолты. PUT делает upsert с частичным слиянием, апдейтит
строку в БД И зовёт `password_policy.apply_policy(...)`, обновляя процессный кэш
активной политики.

Кэш локален процессу. При нескольких репликах остальные подхватят новую политику
через рестарт / rollout (lifespan каждой реплики читает singleton на старте). Для
платформенной настройки такого масштаба это приемлемо — политика меняется редко и
не требует мгновенной согласованности между подами.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core import password_policy
from src.models.password_policy_settings import (
    SINGLETON_ID,
    PasswordPolicySettings,
)
from src.schemas.identity import IdentityContext
from src.schemas.password_policy import (
    PasswordPolicyResponse,
    PasswordPolicyUpdate,
)
from src.services import audit_service


def _to_response(row: PasswordPolicySettings) -> PasswordPolicyResponse:
    return PasswordPolicyResponse(
        min_length=row.min_length,
        require_letter=row.require_letter,
        require_digit=row.require_digit,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _defaults() -> PasswordPolicyResponse:
    policy = password_policy.current_policy()
    return PasswordPolicyResponse(
        min_length=policy["min_length"],
        require_letter=policy["require_letter"],
        require_digit=policy["require_digit"],
    )


async def _get_row(db: AsyncSession) -> PasswordPolicySettings | None:
    return await db.get(PasswordPolicySettings, SINGLETON_ID)


async def get_settings(db: AsyncSession) -> PasswordPolicyResponse:
    """Текущая базовая парольная политика. Нет строки → дефолты кэша."""
    row = await _get_row(db)
    if row is None:
        return _defaults()
    return _to_response(row)


async def update_settings(
    db: AsyncSession,
    identity: IdentityContext,
    patch: PasswordPolicyUpdate,
) -> PasswordPolicyResponse:
    """Upsert политики (частичное слияние) + обновление процессного кэша.

    Неприсланные поля сохраняют текущее значение. После коммита зовём
    `password_policy.apply_policy(...)`, чтобы валидаторы этого процесса сразу
    работали по новой политике. Аудит `password_policy.updated` (WARNING).
    """
    row = await _get_row(db)
    if row is None:
        defaults = _defaults()
        row = PasswordPolicySettings(
            id=SINGLETON_ID,
            min_length=defaults.min_length,
            require_letter=defaults.require_letter,
            require_digit=defaults.require_digit,
        )
        db.add(row)

    if patch.min_length is not None:
        row.min_length = patch.min_length
    if patch.require_letter is not None:
        row.require_letter = patch.require_letter
    if patch.require_digit is not None:
        row.require_digit = patch.require_digit
    row.updated_by = identity.user_id or None

    await db.commit()
    await db.refresh(row)

    password_policy.apply_policy(
        {
            "min_length": row.min_length,
            "require_letter": row.require_letter,
            "require_digit": row.require_digit,
        }
    )

    audit_service.emit(
        "password_policy.updated",
        target_id=SINGLETON_ID,
        target_type="password_policy",
        status="success",
        allowed=True,
        details={
            "min_length": row.min_length,
            "require_letter": row.require_letter,
            "require_digit": row.require_digit,
        },
    )

    return _to_response(row)


async def load_active_policy(db: AsyncSession) -> None:
    """Прочитать singleton из БД и залить его в процессный кэш активной политики.

    Зовётся из lifespan на старте. Если строки ещё нет — кэш остаётся на
    дефолтах, ничего не делаем. Таблицу-«ещё-не-мигрировали» ловит caller
    (обработка ошибок вокруг вызова), чтобы старт не падал на свежей БД.
    """
    row = await _get_row(db)
    if row is None:
        return
    password_policy.apply_policy(
        {
            "min_length": row.min_length,
            "require_letter": row.require_letter,
            "require_digit": row.require_digit,
        }
    )
