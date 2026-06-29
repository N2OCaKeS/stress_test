"""Репозиторий инстанс-уровневого ACL (resource_role_permissions).

Зеркалит `repositories/entity_permission.py`, но ключ — конкретный ресурс
(`resource_type` + `resource_id`), а не тип сущности. Department-scope тот же:
lookup'ы матчат system-wide строки (`department_id IS NULL`) плюс строки
отдела caller'а; writes принимают явный `department_id`.
"""

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ResourceRolePermission


def _dept_match_clause(department_id: str | None):
    """WHERE-условие dept-скоупа для read-lookup'ов.

    `department_id is None` (caller без отдела) → видит только system-wide
    строки. Иначе → system-wide ИЛИ строки своего отдела.
    """
    if department_id is None:
        return ResourceRolePermission.department_id.is_(None)
    return or_(
        ResourceRolePermission.department_id.is_(None),
        ResourceRolePermission.department_id == department_id,
    )


def _scope_clause(department_id: str | None):
    """WHERE-условие для exact-scope (get/grant/revoke одной строки)."""
    if department_id is None:
        return ResourceRolePermission.department_id.is_(None)
    return ResourceRolePermission.department_id == department_id


async def has_resource_action(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
    roles: list[str],
    action: str,
    department_id: str | None,
) -> bool:
    """True iff любая из ролей caller'а имеет инстанс-грант `action` на ресурс."""
    if not roles:
        return False
    stmt = (
        select(ResourceRolePermission.id)
        .where(
            ResourceRolePermission.resource_type == resource_type,
            ResourceRolePermission.resource_id == resource_id,
            ResourceRolePermission.role.in_(roles),
            ResourceRolePermission.action == action,
            _dept_match_clause(department_id),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def effective_resource_actions(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
    roles: list[str],
    department_id: str | None,
) -> set[str]:
    """Set инстанс-грантованных действий для ролей caller'а на ресурсе."""
    if not roles:
        return set()
    stmt = (
        select(ResourceRolePermission.action)
        .where(
            ResourceRolePermission.resource_type == resource_type,
            ResourceRolePermission.resource_id == resource_id,
            ResourceRolePermission.role.in_(roles),
            _dept_match_clause(department_id),
        )
        .distinct()
    )
    return set((await db.execute(stmt)).scalars())


async def resource_ids_with_any_grant(
    db: AsyncSession,
    resource_type: str,
    roles: list[str],
    department_id: str | None,
    *,
    candidate_ids: list[str] | None = None,
) -> set[str]:
    """Множество resource_id, на которые у ролей caller'а есть хоть один грант.

    Для расширения видимости: ресурс виден, если на него есть инстанс-грант.
    `candidate_ids` сужает выборку до конкретного набора (например, до id'шников
    одной страницы списка); None — без ограничения.
    """
    if not roles:
        return set()
    stmt = select(ResourceRolePermission.resource_id).where(
        ResourceRolePermission.resource_type == resource_type,
        ResourceRolePermission.role.in_(roles),
        _dept_match_clause(department_id),
    )
    if candidate_ids is not None:
        if not candidate_ids:
            return set()
        stmt = stmt.where(ResourceRolePermission.resource_id.in_(candidate_ids))
    stmt = stmt.distinct()
    return set((await db.execute(stmt)).scalars())


async def get(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
    department_id: str | None,
) -> ResourceRolePermission | None:
    """Найти строку по точному (resource_type, resource_id, role, action, dept)."""
    stmt = select(ResourceRolePermission).where(
        ResourceRolePermission.resource_type == resource_type,
        ResourceRolePermission.resource_id == resource_id,
        ResourceRolePermission.role == role,
        ResourceRolePermission.action == action,
        _scope_clause(department_id),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_resource(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
    *,
    department_id: str | None = None,
) -> list[ResourceRolePermission]:
    """Список инстанс-грантов на один ресурс в скоупе видимости caller'а.

    `department_id is None` → caller платформенного уровня, видит все строки
    ресурса. Иначе — system-wide плюс строки своего отдела.
    """
    stmt = select(ResourceRolePermission).where(
        ResourceRolePermission.resource_type == resource_type,
        ResourceRolePermission.resource_id == resource_id,
    )
    if department_id is not None:
        stmt = stmt.where(_dept_match_clause(department_id))
    stmt = stmt.order_by(
        ResourceRolePermission.role,
        ResourceRolePermission.action,
        ResourceRolePermission.department_id.is_(None).desc(),
        ResourceRolePermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def list_for_role(
    db: AsyncSession,
    role: str,
    *,
    resource_type: str | None = None,
    department_id: str | None = None,
) -> list[ResourceRolePermission]:
    """Список инстанс-грантов одной роли в скоупе видимости caller'а."""
    stmt = select(ResourceRolePermission).where(
        ResourceRolePermission.role == role,
    )
    if resource_type is not None:
        stmt = stmt.where(ResourceRolePermission.resource_type == resource_type)
    if department_id is not None:
        stmt = stmt.where(_dept_match_clause(department_id))
    stmt = stmt.order_by(
        ResourceRolePermission.resource_type,
        ResourceRolePermission.resource_id,
        ResourceRolePermission.action,
        ResourceRolePermission.department_id.is_(None).desc(),
        ResourceRolePermission.department_id,
    )
    return list((await db.execute(stmt)).scalars())


async def list_all_for_resource_unscoped(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> list[ResourceRolePermission]:
    """Все инстанс-гранты ресурса без dept-фильтра — для propagate (образец)."""
    stmt = select(ResourceRolePermission).where(
        ResourceRolePermission.resource_type == resource_type,
        ResourceRolePermission.resource_id == resource_id,
    )
    return list((await db.execute(stmt)).scalars())


async def grant(
    db: AsyncSession,
    *,
    permission_id: str,
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
    granted_by: str | None,
    department_id: str | None,
) -> ResourceRolePermission:
    """INSERT инстанс-гранта. Scope выбирает caller. commit — на caller'е."""
    obj = ResourceRolePermission(
        id=permission_id,
        resource_type=resource_type,
        resource_id=resource_id,
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
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
    department_id: str | None,
) -> int:
    """DELETE по точному (resource_type, resource_id, role, action, dept)."""
    stmt = delete(ResourceRolePermission).where(
        and_(
            ResourceRolePermission.resource_type == resource_type,
            ResourceRolePermission.resource_id == resource_id,
            ResourceRolePermission.role == role,
            ResourceRolePermission.action == action,
            _scope_clause(department_id),
        )
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def delete_for_resource(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> int:
    """DELETE всех инстанс-грантов ресурса (каскад при удалении объекта)."""
    stmt = delete(ResourceRolePermission).where(
        ResourceRolePermission.resource_type == resource_type,
        ResourceRolePermission.resource_id == resource_id,
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
