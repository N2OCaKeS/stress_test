"""Репозиторий матрицы entity-permissions — lookup'ы и управление grants.

**Department scope.** Lookup'ы и writes department-aware:

* `has_action(... department_id)` принимает department caller'а и матчит
  строки где `entity_permissions.department_id IS NULL OR
  entity_permissions.department_id = :department_id`.
* `get` / `grant` / `revoke` принимают явный `department_id` (None для
  system-wide). Caller'ы (`permission_service`) сами решают, какой scope
  использовать в зависимости от platform-роли актера.

Подробнее — в docstring'е `src/models/entity_permission.py`.
"""

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EntityPermission


def _scope_clause(department_id: str | None):
    """WHERE-условие для exact-scope lookup.

    `department_id IS NULL` → матчит только system-wide строку.
    Иначе → матчит per-department строку для этого department'а.
    """
    if department_id is None:
        return EntityPermission.department_id.is_(None)
    return EntityPermission.department_id == department_id


async def get(
    db: AsyncSession,
    entity_type: str,
    role: str,
    action: str,
    department_id: str | None,
) -> EntityPermission | None:
    """Найти строку по точному (entity_type, role, action, department_id) scope.

    `department_id=None` ищет system-wide строку; не-None — per-department.
    """
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
    """True iff любая из переданных ролей имеет `action` на `entity_type`.

    `department_id` — department caller'а. Матчит *system-wide* строки
    (встроенные роли) **или** *per-department* строки для этого department'а.
    Кастомные роли в чужих отделах невидимы.

    Caller'ы без `department_id` (например, platform-admin без отдела)
    видят только system-wide строки — у них нет per-dept identity для матчинга.
    """
    if not roles:
        return False
    if department_id is None:
        dept_filter = EntityPermission.department_id.is_(None)
    else:
        dept_filter = or_(
            EntityPermission.department_id.is_(None),
            EntityPermission.department_id == department_id,
        )
    stmt = select(EntityPermission.id).where(
        EntityPermission.entity_type == entity_type,
        EntityPermission.role.in_(roles),
        EntityPermission.action == action,
        dept_filter,
    ).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def roles_with_action(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    action: str,
    department_id: str | None,
) -> set[str]:
    """Подмножество `roles`, у которых есть тип-wide `action` на `entity_type`.

    Per-role срез `has_action`: нужен инстанс-ACL'у, чтобы считать базовое
    тип-wide право отдельно для каждой роли caller'а (deny одной роли не должен
    гасить базу другой). Scope-матч как в `has_action`.
    """
    if not roles:
        return set()
    if department_id is None:
        dept_filter = EntityPermission.department_id.is_(None)
    else:
        dept_filter = or_(
            EntityPermission.department_id.is_(None),
            EntityPermission.department_id == department_id,
        )
    stmt = (
        select(EntityPermission.role)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            EntityPermission.action == action,
            dept_filter,
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


async def roles_with_other_action(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    department_id: str | None,
    *,
    exclude_action: str,
) -> set[str]:
    """Подмножество `roles` с тип-wide правом на любой action, кроме `exclude_action`.

    Нужно неявному view: роль с тип-wide правом на не-view действие
    (например `power_on`) получает тип-wide view на этот тип. Scope-матч как
    в `has_action`.
    """
    if not roles:
        return set()
    if department_id is None:
        dept_filter = EntityPermission.department_id.is_(None)
    else:
        dept_filter = or_(
            EntityPermission.department_id.is_(None),
            EntityPermission.department_id == department_id,
        )
    stmt = (
        select(EntityPermission.role)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            EntityPermission.action != exclude_action,
            dept_filter,
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


async def effective_actions(
    db: AsyncSession,
    entity_type: str,
    roles: list[str],
    department_id: str | None,
) -> set[str]:
    """Set действий, которые суммарно дают переданные роли в скоупе caller'а.

    Тот же scope, что и в `has_action`: system-wide строки матчат всем,
    per-department — только caller'у из того же отдела. SELECT DISTINCT,
    чтобы не тянуть лишние строки матрицы в Python.
    """
    if not roles:
        return set()
    if department_id is None:
        dept_filter = EntityPermission.department_id.is_(None)
    else:
        dept_filter = or_(
            EntityPermission.department_id.is_(None),
            EntityPermission.department_id == department_id,
        )
    stmt = (
        select(EntityPermission.action)
        .where(
            EntityPermission.entity_type == entity_type,
            EntityPermission.role.in_(roles),
            dept_filter,
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


def _visible_scope_clause(department_id: str | None):
    """WHERE-условие для list-выборки в скоупе одного department'а.

    `department_id is None` → caller — platform-уровневый (без отдела),
    видит ВСЕ строки (фильтр не накладывается). Иначе — caller видит только
    system-wide строки и строки своего отдела.

    Возвращает либо SQL-условие, либо None (для случая «без фильтра»).
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

    `department_id=None` → выдаём всю матрицу (только для platform-админов,
    не привязанных к отделу). `department_id="dep_X"` → отдаём system-wide
    строки плюс строки этого отдела; чужие отделы невидимы.

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
        EntityPermission.department_id.is_(None).desc(),  # system-wide первыми
        EntityPermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def list_for_entity(
    db: AsyncSession,
    entity_type: str,
    *,
    department_id: str | None = None,
) -> list[EntityPermission]:
    """Список grants одного entity_type, в скоупе видимости caller'а."""
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
    """INSERT permission-строки. Scope выбирает caller.

    * `department_id=None` — system-wide (только account_admin должен передавать).
    * `department_id="dep_X"` — per-department (свой dept у department/service admin).
    """
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
