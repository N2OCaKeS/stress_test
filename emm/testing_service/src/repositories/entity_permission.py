"""Репозиторий матрицы entity-permissions — lookup'ы и управление grants.

Scope-правило то же, что в `server_service`: system-wide строки
(`department_id IS NULL`) матчатся всем, per-department — только caller'у из
того же отдела. Это закрывает cross-dept утечку прав через совпадение имён
кастомных ролей.

Write-side (`grant`/`revoke`) вызывается только из `services/permission_service`
через эндпоинты `/permissions*` — остальные бизнес-сервисы сюда не ходят, им
нужен только read-side (`has_action`/`effective_actions`).
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


def _caller_dept_filter(department_id: str | None):
    """WHERE-условие для проверки прав caller'а из `department_id`.

    В отличие от `_visible_scope_clause` (список для UI), это фильтр
    авторизации: `department_id=None` здесь значит «caller без отдела» и
    матчит только system-wide строки — не «показать всё».
    """
    if department_id is None:
        return EntityPermission.department_id.is_(None)
    return or_(
        EntityPermission.department_id.is_(None),
        EntityPermission.department_id == department_id,
    )


def _visible_scope_clause(department_id: str | None):
    """WHERE-условие видимости строк для list-выборок (каталог для UI/ИБ).

    `department_id is None` → caller — platform-уровневый (account_admin без
    отдела), видит всю матрицу (фильтр не накладывается, возвращает None).
    Иначе — caller видит system-wide строки плюс строки своего отдела.
    """
    if department_id is None:
        return None
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
            _caller_dept_filter(department_id),
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
            _caller_dept_filter(department_id),
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


async def get(
    db: AsyncSession,
    entity_type: str,
    role: str,
    action: str,
    department_id: str | None,
) -> EntityPermission | None:
    """Найти строку по точному (entity_type, role, action, department_id) scope.

    `department_id=None` ищет system-wide строку; не-None — per-department.
    Нужен для идемпотентности `grant`: повторный запрос в тот же scope должен
    вернуть existing, а не упасть на UNIQUE.
    """
    stmt = select(EntityPermission).where(
        EntityPermission.entity_type == entity_type,
        EntityPermission.role == role,
        EntityPermission.action == action,
        _scope_clause(department_id),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_all(
    db: AsyncSession,
    *,
    department_id: str | None = None,
) -> list[EntityPermission]:
    """Список grants в скоупе видимости caller'а.

    `department_id=None` → выдаём всю матрицу (только для account_admin, не
    привязанного к отделу). `department_id="dep_X"` → system-wide строки плюс
    строки этого отдела; чужие отделы невидимы.

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
