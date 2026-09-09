"""Репозиторий матрицы entity-permissions — read-side lookup'ы.

Scope-правило то же, что в `server_service`: system-wide строки
(`department_id IS NULL`) матчатся всем, per-department — только caller'у из
того же отдела. Это закрывает cross-dept утечку прав через совпадение имён
кастомных ролей.

Write-side (grant/revoke через API) появится вместе с эндпоинтами управления
матрицей; сейчас строки пишет только миграция-сид.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EntityPermission


def _visible_scope_clause(department_id: str | None):
    """WHERE-условие видимости строк для caller'а из `department_id`."""
    if department_id is None:
        return EntityPermission.department_id.is_(None)
    return or_(
        EntityPermission.department_id.is_(None),
        EntityPermission.department_id == department_id,
    )


async def has_action(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    action: str,
    department_id: str | None,
) -> bool:
    """True iff любая из ролей имеет `action` на `entity_type` в скоупе caller'а."""
    if not roles:
        return False
    stmt = (
        select(EntityPermission.id)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            EntityPermission.action == action,
            _visible_scope_clause(department_id),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def effective_actions(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    department_id: str | None,
) -> set[str]:
    """Set действий, которые суммарно дают роли caller'а на `entity_type`."""
    if not roles:
        return set()
    stmt = (
        select(EntityPermission.action)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            _visible_scope_clause(department_id),
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())
