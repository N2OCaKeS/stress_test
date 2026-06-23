"""Runtime-override политики brute-force lockout'а.

Эффективные параметры (`max_attempts`, `lockout_minutes`) берутся из БД-строки
`lockout_policy`, если она есть; иначе — из env-конфига (`core.config`). Логин-
pipeline (`auth_service.verify_password_with_lockout`, self-change-password в
`user_service`) дёргает `resolve_lockout_policy`, а не читает settings напрямую,
чтобы account_admin мог крутить политику без рестарта.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.repositories.lockout_policy import LockoutPolicyRepository
from src.services import audit_service


async def resolve_lockout_policy(db: AsyncSession) -> tuple[int, int]:
    """Эффективные `(max_attempts, lockout_minutes)`: БД-override либо env-дефолт."""
    repo = LockoutPolicyRepository(db)
    row = await repo.get()
    if row is not None:
        return row.max_failed_attempts, row.lockout_minutes
    settings = get_settings()
    return settings.max_failed_login_attempts, settings.lockout_minutes


async def get_effective_policy(db: AsyncSession) -> tuple[int, int, str]:
    """Как `resolve_lockout_policy`, плюс источник: `"db"` или `"env"`."""
    repo = LockoutPolicyRepository(db)
    row = await repo.get()
    if row is not None:
        return row.max_failed_attempts, row.lockout_minutes, "db"
    settings = get_settings()
    return settings.max_failed_login_attempts, settings.lockout_minutes, "env"


async def update_policy(
    db: AsyncSession,
    actor_id: str,
    max_failed_attempts: int,
    lockout_minutes: int,
    request_id: str | None = None,
) -> tuple[int, int]:
    """Записать БД-override политики. Возвращает новые эффективные значения.

    Старые значения для audit'а берём из текущего эффективного состояния
    (БД-строка или env-дефолт), чтобы в trail было видно фактический переход.
    """
    repo = LockoutPolicyRepository(db)
    old_max, old_minutes, _ = await get_effective_policy(db)

    await repo.upsert(
        max_failed_attempts=max_failed_attempts,
        lockout_minutes=lockout_minutes,
        updated_by=actor_id,
    )
    await db.commit()

    audit_service.emit(
        "lockout_policy.update",
        actor_id,
        target_id=None,
        target_type="lockout_policy",
        details={
            "old_max": old_max,
            "old_minutes": old_minutes,
            "new_max": max_failed_attempts,
            "new_minutes": lockout_minutes,
        },
        request_id=request_id,
    )
    return max_failed_attempts, lockout_minutes
