"""Управление инстанс-уровневым ACL — list/grant/revoke/propagate (write-side).

CRUD по `resource_role_permissions`: точечные гранты роли на КОНКРЕТНЫЙ ресурс
(server / server_account). Слой поверх тип-wide матрицы `entity_permissions`;
read-side проверки (`has_resource_action`, `effective_resource_actions`) живут в
:mod:`src.services.permissions`.

**Авторизация** — как у `permission_service`: платформенный `account_admin` —
мета-админ (bypass ролевой проверки и dept-isolation), остальным нужен
`(permission, *, permission_grant)` / `permission_revoke` и принадлежность
ресурса своему отделу.

**Department scope.** Инстанс-грант всегда per-department: `department_id`
строки = `department_id` самого ресурса. System-wide инстанс-грантов через API
нет (как и в матрице — они приходят только из seed-миграций). Поэтому
department-bound caller обязан оперировать ресурсом своего отдела; чужой отдел —
404 (не раскрываем существование).

**Какие действия можно грантовать.** Только инстанс-привязанные
(`constants.INSTANCE_GRANTABLE_ACTIONS`): `create` и callback-действия воркера
остаются в глобальном слое и сюда не принимаются (422 ACTION_NOT_INSTANCE_GRANTABLE).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    Action,
    EntityType,
    PlatformRole,
    RESOURCE_ACL_TYPES,
    is_instance_grantable,
)
from src.core.exceptions import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import ResourceRolePermission
from src.repositories import resource_role_permission as repo
from src.repositories import server as server_repo
from src.repositories import server_account as account_repo
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import resource_role_permission_id as new_id


def _is_matrix_meta_admin(identity: IdentityContext) -> bool:
    """True для платформенного `account_admin` — мета-админа ACL."""
    return identity.platform_role == PlatformRole.ACCOUNT_ADMIN


def _validate_resource_type(resource_type: str) -> None:
    """Отбить неизвестный resource_type (вне RESOURCE_ACL_TYPES) → 422."""
    if resource_type not in RESOURCE_ACL_TYPES:
        raise DomainValidationError(
            error_code="UNKNOWN_RESOURCE_TYPE",
            message=(
                f"resource_type '{resource_type}' does not support instance ACL "
                f"(allowed: {sorted(RESOURCE_ACL_TYPES)})"
            ),
            details={"resource_type": resource_type},
        )


async def _load_resource_department(
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> str | None:
    """Вернуть department_id ресурса или None, если ресурса нет."""
    if resource_type == EntityType.SERVER:
        obj = await server_repo.get_by_id(db, resource_id)
    else:
        obj = await account_repo.get_by_id(db, resource_id)
    return obj.department_id if obj is not None else None


async def _resolve_resource_scope(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    *,
    audit_action: str,
    audit_details: dict,
) -> str:
    """Проверить существование ресурса + его принадлежность отделу актора.

    Возвращает `department_id` ресурса (scope будущей строки). Ресурс другого
    отдела / несуществующий — 404 RESOURCE_NOT_FOUND (не раскрываем
    существование). account_admin (мета-админ) видит ресурсы любого отдела.
    """
    resource_dept = await _load_resource_department(db, resource_type, resource_id)
    visible = resource_dept is not None and (
        _is_matrix_meta_admin(identity)
        or resource_dept == identity.department_id
    )
    if not visible:
        audit_service.emit(
            audit_action,
            target_id=resource_id, target_type=resource_type,
            status="failure", allowed=True,
            details={**audit_details, "reason": "resource_not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="RESOURCE_NOT_FOUND",
            message=f"{resource_type} not found",
            details={"resource_type": resource_type, "resource_id": resource_id},
        )
    return resource_dept


def _read_scope(identity: IdentityContext) -> str | None:
    """Scope для list-выборок: None → platform-уровневый видит всё."""
    if _is_matrix_meta_admin(identity):
        return None
    return identity.department_id


async def list_for_resource(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
) -> list[ResourceRolePermission]:
    """Список инстанс-грантов на один ресурс. Требует `view` на `permission`."""
    if not _is_matrix_meta_admin(identity):
        await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    _validate_resource_type(resource_type)
    return await repo.list_for_resource(
        db, resource_type, resource_id, department_id=_read_scope(identity)
    )


async def list_for_role(
    db: AsyncSession,
    identity: IdentityContext,
    role: str,
    *,
    resource_type: str | None = None,
) -> list[ResourceRolePermission]:
    """Список инстанс-грантов одной роли. Требует `view` на `permission`."""
    if not _is_matrix_meta_admin(identity):
        await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    if resource_type is not None:
        _validate_resource_type(resource_type)
    return await repo.list_for_role(
        db, role, resource_type=resource_type, department_id=_read_scope(identity)
    )


async def grant_action(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
) -> ResourceRolePermission:
    """Выдать инстанс-грант `action` роли `role` на ресурс.

    Идемпотентно: повторный grant того же scope возвращает существующую строку
    без INSERT'а и без audit-emit (как `permission_service.grant_action`).
    """
    audit_details = {
        "resource_type": resource_type, "resource_id": resource_id,
        "role": role, "action": action,
    }
    # 1. ролевой gate (account_admin — мета-админ, ему не нужен).
    if not _is_matrix_meta_admin(identity):
        with emit_denied_on_authz_error(
            "resource_permission.grant",
            target_id=resource_id, target_type=resource_type,
            extra_details=dict(audit_details),
            identity=identity,
        ):
            await permissions.require_action(
                db, identity, EntityType.PERMISSION, Action.PERMISSION_GRANT
            )
    # 2. resource_type + action-инстанс-грантуемость.
    _validate_resource_type(resource_type)
    if not is_instance_grantable(resource_type, action):
        audit_service.emit(
            "resource_permission.grant", target_id=resource_id, target_type=resource_type,
            status="failure", allowed=True,
            details={**audit_details, "reason": "action_not_instance_grantable"},
        )
        raise DomainValidationError(
            error_code="ACTION_NOT_INSTANCE_GRANTABLE",
            message=(
                f"Action '{action}' cannot be granted per-instance on "
                f"'{resource_type}' (global-only action — use entity_permissions)"
            ),
            details={"resource_type": resource_type, "action": action},
        )
    # 3. существование ресурса + dept-isolation → scope.
    department_id = await _resolve_resource_scope(
        db, identity, resource_type, resource_id,
        audit_action="resource_permission.grant", audit_details=audit_details,
    )
    scope_details = {**audit_details, "department_id": department_id}
    # 4. идемпотентность.
    existing = await repo.get(db, resource_type, resource_id, role, action, department_id)
    if existing is not None:
        return existing
    try:
        obj = await repo.grant(
            db,
            permission_id=new_id(),
            resource_type=resource_type,
            resource_id=resource_id,
            role=role,
            action=action,
            granted_by=identity.user_id,
            department_id=department_id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "resource_permission.grant", target_id=resource_id, target_type=resource_type,
            status="failure", allowed=True,
            details={**scope_details, "reason": "race_already_exists"},
        )
        raise ConflictError(
            error_code="RESOURCE_PERMISSION_ALREADY_EXISTS",
            message=(
                "Instance grant already exists for this "
                "(resource_type, resource_id, role, action, department_id)"
            ),
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "resource_permission.grant",
        target_id=obj.id, target_type=resource_type,
        status="success", allowed=True,
        details=scope_details,
    )
    return obj


async def revoke_action(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    resource_type: str,
    resource_id: str,
    role: str,
    action: str,
) -> None:
    """Снять инстанс-грант. Отсутствие строки → 404 RESOURCE_PERMISSION_NOT_FOUND."""
    audit_details = {
        "resource_type": resource_type, "resource_id": resource_id,
        "role": role, "action": action,
    }
    if not _is_matrix_meta_admin(identity):
        with emit_denied_on_authz_error(
            "resource_permission.revoke",
            target_id=resource_id, target_type=resource_type,
            extra_details=dict(audit_details),
            identity=identity,
        ):
            await permissions.require_action(
                db, identity, EntityType.PERMISSION, Action.PERMISSION_REVOKE
            )
    _validate_resource_type(resource_type)
    department_id = await _resolve_resource_scope(
        db, identity, resource_type, resource_id,
        audit_action="resource_permission.revoke", audit_details=audit_details,
    )
    scope_details = {**audit_details, "department_id": department_id}
    removed = await repo.revoke(db, resource_type, resource_id, role, action, department_id)
    if removed == 0:
        await db.rollback()
        audit_service.emit(
            "resource_permission.revoke", target_id=resource_id, target_type=resource_type,
            status="failure", allowed=True,
            details={**scope_details, "reason": "not_found"},
        )
        raise NotFoundError(
            error_code="RESOURCE_PERMISSION_NOT_FOUND",
            message=(
                "No instance grant matched "
                "(resource_type, resource_id, role, action, department_id)"
            ),
            details=scope_details,
        )
    await db.commit()
    audit_service.emit(
        "resource_permission.revoke", target_id=resource_id, target_type=resource_type,
        status="success", allowed=True,
        details=scope_details,
    )


async def propagate(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    resource_type: str,
    source_resource_id: str,
    target_resource_ids: list[str],
    mode: str,
) -> dict:
    """Скопировать ВСЕ инстанс-гранты образца на список целей того же типа.

    * ``merge`` — добавить на каждую цель недостающие `(role, action)` образца;
      существующие не трогать.
    * ``mirror`` — привести набор целей к точной копии образца (добавить
      недостающие + удалить лишние).

    Идемпотентно. Возвращает dict под `ResourcePropagateResponse`.
    """
    audit_details = {
        "resource_type": resource_type,
        "source_resource_id": source_resource_id,
        "mode": mode,
        "target_count": len(target_resource_ids),
    }
    # 1. ролевой gate: grant всегда; mirror дополнительно удаляет → revoke.
    if not _is_matrix_meta_admin(identity):
        with emit_denied_on_authz_error(
            "resource_permission.propagate",
            target_id=source_resource_id, target_type=resource_type,
            extra_details=dict(audit_details),
            identity=identity,
        ):
            await permissions.require_action(
                db, identity, EntityType.PERMISSION, Action.PERMISSION_GRANT
            )
            if mode == "mirror":
                await permissions.require_action(
                    db, identity, EntityType.PERMISSION, Action.PERMISSION_REVOKE
                )
    _validate_resource_type(resource_type)

    # 2. образец: существование + dept-isolation → scope (отдел propagate'а).
    source_dept = await _resolve_resource_scope(
        db, identity, resource_type, source_resource_id,
        audit_action="resource_permission.propagate", audit_details=audit_details,
    )

    # 3. цели: каждая должна существовать и быть в том же отделе, что и образец
    #    (иначе нарушим dept-scope грантов). Самого образца в списке быть не
    #    должно — пропускаем его, если прислали.
    source_grants = await repo.list_all_for_resource_unscoped(
        db, resource_type, source_resource_id
    )
    source_pairs: set[tuple[str, str]] = {(g.role, g.action) for g in source_grants}

    summaries: list[dict] = []
    seen: set[str] = set()
    for target_id in target_resource_ids:
        if target_id == source_resource_id or target_id in seen:
            continue
        seen.add(target_id)
        target_dept = await _load_resource_department(db, resource_type, target_id)
        if target_dept is None or target_dept != source_dept:
            audit_service.emit(
                "resource_permission.propagate",
                target_id=target_id, target_type=resource_type,
                status="failure", allowed=True,
                details={
                    **audit_details, "reason": "target_not_found_or_cross_dept",
                    "target_resource_id": target_id,
                },
            )
            raise NotFoundError(
                error_code="RESOURCE_NOT_FOUND",
                message=f"{resource_type} not found",
                details={"resource_type": resource_type, "resource_id": target_id},
            )

        existing = await repo.list_all_for_resource_unscoped(db, resource_type, target_id)
        existing_pairs: set[tuple[str, str]] = {(g.role, g.action) for g in existing}

        to_add = source_pairs - existing_pairs
        for role, action in sorted(to_add):
            await repo.grant(
                db,
                permission_id=new_id(),
                resource_type=resource_type,
                resource_id=target_id,
                role=role,
                action=action,
                granted_by=identity.user_id,
                department_id=target_dept,
            )

        removed = 0
        if mode == "mirror":
            to_remove = existing_pairs - source_pairs
            for role, action in to_remove:
                # Снимаем per-dept грант цели (scope = отдел ресурса). API других
                # инстанс-грантов и не создаёт, поэтому mirror приводит набор к
                # копии образца. Возможные system-wide (NULL) seed-строки mirror
                # не трогает — они вне dept-scope.
                removed += await repo.revoke(
                    db, resource_type, target_id, role, action, target_dept
                )
        summaries.append({
            "resource_id": target_id,
            "added": len(to_add),
            "removed": removed,
        })

    await db.commit()
    audit_service.emit(
        "resource_permission.propagate",
        target_id=source_resource_id, target_type=resource_type,
        status="success", allowed=True,
        details={
            **audit_details,
            "department_id": source_dept,
            "source_grant_count": len(source_pairs),
            "targets_applied": len(summaries),
            "total_added": sum(s["added"] for s in summaries),
            "total_removed": sum(s["removed"] for s in summaries),
        },
    )
    return {
        "source_resource_id": source_resource_id,
        "resource_type": resource_type,
        "mode": mode,
        "source_grant_count": len(source_pairs),
        "targets": summaries,
    }
