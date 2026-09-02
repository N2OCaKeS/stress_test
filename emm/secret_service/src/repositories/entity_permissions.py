"""Репозиторий матрицы entity-permissions — lookup'ы и управление grants.

Lookup'ы и writes department-aware:

* `has_action` / `effective_actions` принимают department, к которому
  относится проверка (у secret_service — отдел-владелец секрета) и матчат
  строки где `department_id IS NULL OR department_id = :department_id`.
* `get` / `grant` / `revoke` принимают явный `department_id` (None для
  system-wide). Scope выбирает caller (`permission_service`).

Подробнее — в docstring'е `src/models/entity_permission.py`.
"""

from __future__ import annotations

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EntityPermission


def _scope_clause(department_id: str | None):
    """WHERE для exact-scope lookup: None → только system-wide строка."""
    if department_id is None:
        return EntityPermission.department_id.is_(None)
    return EntityPermission.department_id == department_id


def _match_clause(department_id: str | None):
    """WHERE для проверки доступа: system-wide строки + строки этого отдела."""
    if department_id is None:
        return EntityPermission.department_id.is_(None)
    return or_(
        EntityPermission.department_id.is_(None),
        EntityPermission.department_id == department_id,
    )


async def get(
    db: AsyncSession,
    entity_type: str,
    role: str,
    action: str,
    department_id: str | None,
) -> EntityPermission | None:
    """Найти строку по точному (entity_type, role, action, department_id) scope."""
    stmt = select(EntityPermission).where(
        EntityPermission.entity_type == entity_type,
        EntityPermission.role == role,
        EntityPermission.action == action,
        _scope_clause(department_id),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def has_action(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    action: str,
    department_id: str | None,
) -> bool:
    """True iff любая из ролей имеет `action` на `entity_type` в скоупе."""
    if not roles:
        return False
    stmt = (
        select(EntityPermission.id)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            EntityPermission.action == action,
            _match_clause(department_id),
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
    """Set действий, которые суммарно дают переданные роли в скоупе.

    Тот же scope, что и в `has_action`: system-wide строки матчат всем,
    per-department — только для переданного отдела.
    """
    if not roles:
        return set()
    stmt = (
        select(EntityPermission.action)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            _match_clause(department_id),
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


def _visible_scope_clause(department_id: str | None):
    """WHERE для list-выборки в скоупе видимости caller'а.

    `None` → caller platform-уровневый (без отдела), видит всю матрицу.
    Иначе — только system-wide строки и строки своего отдела.
    """
    if department_id is None:
        return None
    return or_(
        EntityPermission.department_id.is_(None),
        EntityPermission.department_id == department_id,
    )


async def list_all(
    db: AsyncSession,
    *,
    department_id: str | None = None,
) -> list[EntityPermission]:
    """Список grants в скоупе видимости caller'а.

    Order: (entity_type, role, action, system_first, department_id).
    """
    stmt = select(EntityPermission)
    scope = _visible_scope_clause(department_id)
    if scope is not None:
        stmt = stmt.where(scope)
    stmt = stmt.order_by(
        EntityPermission.entity_type,
        EntityPermission.role,
        EntityPermission.action,
        EntityPermission.department_id.is_(None).desc(),
        EntityPermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def list_for_role(
    db: AsyncSession,
    role: str,
    *,
    department_id: str | None = None,
) -> list[EntityPermission]:
    """Список grants одной роли в скоупе видимости caller'а."""
    stmt = select(EntityPermission).where(EntityPermission.role == role)
    scope = _visible_scope_clause(department_id)
    if scope is not None:
        stmt = stmt.where(scope)
    stmt = stmt.order_by(
        EntityPermission.entity_type,
        EntityPermission.action,
        EntityPermission.department_id.is_(None).desc(),
        EntityPermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def list_for_entity(
    db: AsyncSession,
    entity_type: str,
    *,
    department_id: str | None = None,
) -> list[EntityPermission]:
    """Список grants одного entity_type в скоупе видимости caller'а."""
    stmt = select(EntityPermission).where(
        EntityPermission.entity_type == entity_type,
    )
    scope = _visible_scope_clause(department_id)
    if scope is not None:
        stmt = stmt.where(scope)
    stmt = stmt.order_by(
        EntityPermission.role,
        EntityPermission.action,
        EntityPermission.department_id.is_(None).desc(),
        EntityPermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def grant(
    db: AsyncSession,
    *,
    permission_id: str,
    entity_type: str,
    role: str,
    action: str,
    granted_by: str | None,
    department_id: str | None,
) -> EntityPermission:
    """INSERT permission-строки. commit — на caller'е."""
    obj = EntityPermission(
        id=permission_id,
        entity_type=entity_type,
        role=role,
        action=action,
        granted_by=granted_by,
        department_id=department_id,
    )
    db.add(obj)
    await db.flush()
    return obj


async def revoke(
    db: AsyncSession,
    entity_type: str,
    role: str,
    action: str,
    department_id: str | None,
) -> int:
    """DELETE строки по точному (entity_type, role, action, department_id)."""
    stmt = delete(EntityPermission).where(
        and_(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role == role,
            EntityPermission.action == action,
            _scope_clause(department_id),
        )
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
