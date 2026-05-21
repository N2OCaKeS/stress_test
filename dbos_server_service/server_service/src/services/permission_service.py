"""Управление матрицей прав — list/grant/revoke (write-side).

Это **write-side** модуль: CRUD по `entity_permissions`. Использует
read-side хелперы из :mod:`src.services.permissions` (`require_action`)
для авторизации каждой своей операции. Дёргают только endpoint'ы из
`api/v1/endpoints/permissions.py`; бизнес-сервисы (server / ipmi / disk
и т.д.) сюда не ходят — им нужен только `permissions.has_action`.

**Department scope.** `grant_action` / `revoke_action` кладут строки
исключительно per-department: department/service admin пишет в свой
собственный department. Передача другого ``target_department_id``
отбивается 403 ``DEPARTMENT_ISOLATION`` и аудитится как
``permission.grant`` denied с ``reason=department_isolation_grant``
(или ``..._revoke``).

System-wide строки (``department_id IS NULL``) есть только в seed-миграциях
для встроенных ролей — через runtime endpoint их не создать. Platform-роли
(``account_admin``/``loging_admin``) до этого слоя не доходят: их режет
``platform_admin_guard`` middleware ещё на входе.

Caller без ``department_id`` (edge-кейс — `loging_reader` или прочие
platform-bound токены без отдела) писать не может и обрабатывается как
denied.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, is_valid_action
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import EntityPermission
from src.repositories import entity_permission as repo
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import entity_permission_id as new_id


def _resolve_target_department_id(
    identity: IdentityContext,
    target_department_id: str | None,
    *,
    audit_action: str,
    audit_details: dict,
    isolation_reason: str,
) -> str | None:
    """Определить department-scope для записи.

    Возвращает `department_id` для новой строки, либо бросает
    :class:`AuthorizationError` с кодом ``DEPARTMENT_ISOLATION``, если caller
    не имеет права писать в запрошенный scope. Эмитит denied-audit ДО raise.

    Caller обязан быть department-bound (`identity.department_id != None`).
    Все writes scoped на ``identity.department_id``; передача другого
    ``target_department_id`` → ``DEPARTMENT_ISOLATION``. System-wide grant'ов
    через этот путь нет — они приходят только из seed-миграций.
    """
    actor_dept = identity.department_id
    if actor_dept is None:
        # Caller без department'а вообще. Трактуем как isolation violation,
        # а не как generic permission denial — caller *пытается* писать,
        # но у него нет своего scope'а.
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_department_id": None,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message=(
                "Caller has no department; only account_admin may manage "
                "system-wide entity_permissions"
            ),
            details={"target_department_id": target_department_id},
        )
    if target_department_id is not None and target_department_id != actor_dept:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message=(
                "Cannot manage entity_permissions for a different department"
            ),
            details={
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
    return actor_dept


async def list_all(db: AsyncSession, identity: IdentityContext) -> list[EntityPermission]:
    """Список всех grants. Требует `view` на entity `permission`."""
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    return await repo.list_all(db)


async def list_for_entity(
    db: AsyncSession, identity: IdentityContext, entity_type: str
) -> list[EntityPermission]:
    """Список grants для одного entity_type. Неизвестный type → 422."""
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    if entity_type not in {e.value for e in EntityType}:
        raise DomainValidationError(
            error_code="UNKNOWN_ENTITY_TYPE",
            message=f"Unknown entity_type '{entity_type}'",
        )
    return await repo.list_for_entity(db, entity_type)


async def grant_action(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> EntityPermission:
    """Выдать `action` на `entity_type` для `role`.

    Scope (см. module docstring): department/service admin неявно нацеливается
    на свой department; не совпадающий ``target_department_id`` → 403
    ``DEPARTMENT_ISOLATION``.

    Идемпотентность: повторный grant в тот же scope возвращает существующую
    строку без INSERT'а (`noop` в audit details).
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    # 1. проверка matrix-уровня
    with emit_denied_on_authz_error(
        "permission.grant",
        target_type="entity_permission",
        extra_details=dict(audit_details),
    ):
        await permissions.require_action(
            db, identity, EntityType.PERMISSION, Action.PERMISSION_GRANT
        )
    # 2. определяем department-scope (и enforce'им изоляцию для не-account_admin)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.grant",
        audit_details=audit_details,
        isolation_reason="department_isolation_grant",
    )
    # 3. action-vs-entity whitelist
    if not is_valid_action(entity_type, action):
        audit_service.emit(
            "permission.grant", target_type="entity_permission",
            status="failure", allowed=True,
            details={**audit_details, "reason": "invalid_action_for_entity"},
        )
        raise DomainValidationError(
            error_code="INVALID_ACTION_FOR_ENTITY",
            message=f"Action '{action}' is not valid for entity_type '{entity_type}'",
            details={"entity_type": entity_type, "action": action},
        )
    scope_details = {**audit_details, "department_id": department_id}
    # 4. идемпотентность — exact-scope lookup
    existing = await repo.get(db, entity_type, role, action, department_id)
    if existing is not None:
        audit_service.emit(
            "permission.grant",
            target_id=existing.id, target_type="entity_permission",
            status="success", allowed=True,
            details={**scope_details, "noop": True},
        )
        return existing
    try:
        obj = await repo.grant(
            db,
            permission_id=new_id(),
            entity_type=entity_type,
            role=role,
            action=action,
            granted_by=identity.user_id,
            department_id=department_id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "permission.grant", target_type="entity_permission",
            status="failure", allowed=True,
            details={**scope_details, "reason": "race_already_exists"},
        )
        raise ConflictError(
            error_code="PERMISSION_ALREADY_EXISTS",
            message=(
                "Permission row already exists for this "
                "(entity_type, role, action, department_id)"
            ),
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "permission.grant",
        target_id=obj.id, target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
    return obj


async def revoke_action(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> None:
    """Снять существующий grant. Scope-семантика как у :func:`grant_action`.

    Отсутствие строки → 404 PERMISSION_NOT_FOUND (а не noop) — чтобы клиент
    знал, что revoke не удалил то, что ожидал.
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    with emit_denied_on_authz_error(
        "permission.revoke",
        target_type="entity_permission",
        extra_details=dict(audit_details),
    ):
        await permissions.require_action(
            db, identity, EntityType.PERMISSION, Action.PERMISSION_REVOKE
        )
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.revoke",
        audit_details=audit_details,
        isolation_reason="department_isolation_revoke",
    )
    scope_details = {**audit_details, "department_id": department_id}
    removed = await repo.revoke(db, entity_type, role, action, department_id)
    await db.commit()
    if removed == 0:
        audit_service.emit(
            "permission.revoke", target_type="entity_permission",
            status="failure", allowed=True,
            details={**scope_details, "reason": "not_found"},
        )
        raise NotFoundError(
            error_code="PERMISSION_NOT_FOUND",
            message=(
                "No permission row matched "
                "(entity_type, role, action, department_id)"
            ),
            details={
                "entity_type": entity_type,
                "role": role,
                "action": action,
                "department_id": department_id,
            },
        )
    audit_service.emit(
        "permission.revoke", target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
