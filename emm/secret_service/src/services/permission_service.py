"""Управление тип-wide матрицей прав secret_service — list/grant/revoke/catalog.

Write-side CRUD по `entity_permissions`. Матрица раздаёт роли действие на
неличные секреты отдела; `access_service.check_access` консультируется с ней
как с базовым источником allow (см. `access_service._matrix_allows`).

**Кто управляет матрицей.** У secret_service нет self-референсной сущности
`permission`, как в server_service. Право управлять матрицей несут:

* платформенный ``account_admin`` — мета-админ: видит и правит матрицу любого
  отдела, целевой отдел задаётся ``target_department_id`` без dept-isolation;
* ``department_admin`` своего отдела;
* носитель сервисной роли ``admin`` secret_service'а своего отдела.

Остальные — 403 ``PERMISSION_DENIED``. Bot / oauth_client матрицу не правят.

**Department scope.** Department-bound caller пишет только в свой отдел;
передача чужого ``target_department_id`` → 403 ``DEPARTMENT_ISOLATION``.
System-wide строки (``department_id IS NULL``) существуют только для системных
ролей и сеются миграциями.

**Системные роли.** ``guest``/``admin`` неизменяемы — grant/revoke по ним
отбивается 409 ``SYSTEM_ROLE_IMMUTABLE``.
"""

from __future__ import annotations

import secrets as _secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ENTITY_ACTIONS,
    EntityType,
    SecretAction,
    is_system_role,
    is_valid_action,
)
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.core.permission_catalog import (
    ACTION_DESCRIPTIONS,
    ENTITY_DESCRIPTIONS,
    SENSITIVE_ACTIONS,
)
from src.dependencies.auth import Identity
from src.models import EntityPermission
from src.repositories import entity_permissions as repo
from src.services import audit_service

SERVICE_NAME = "secret_service"


def _new_permission_id() -> str:
    return f"prm_{_secrets.token_hex(16)}"


# ── Authorization helpers ────────────────────────────────────────────────────


def _is_meta_admin(identity: Identity) -> bool:
    """True для платформенного `account_admin` — мета-админа матрицы.

    Он управляет матрицей любого отдела (просмотр + grant/revoke), сами секреты
    не оперирует. У него нет отдела и сервисных ролей, поэтому dept-isolation к
    нему неприменима — scope записи равен переданному target'у as-is.
    """
    return identity.platform_role == "account_admin"


def _can_manage_matrix(identity: Identity) -> bool:
    """Может ли caller видеть/править матрицу.

    account_admin (любой отдел) либо department-bound admin своего отдела:
    `department_admin` или носитель сервисной роли `admin` secret_service'а.
    """
    if _is_meta_admin(identity):
        return True
    if identity.department_id is None:
        return False
    if identity.platform_role == "department_admin":
        return True
    return "admin" in identity.roles_for(SERVICE_NAME)


def _require_matrix_access(identity: Identity) -> None:
    """Отбить caller'а без права управлять матрицей (403 PERMISSION_DENIED)."""
    if _can_manage_matrix(identity):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message="Caller has no permission to manage the secret_service permission matrix",
    )


def _read_scope(identity: Identity) -> str | None:
    """Scope для list-выборок: None → вся матрица (account_admin), иначе — отдел."""
    if _is_meta_admin(identity):
        return None
    return identity.department_id


def _resolve_target_department_id(
    identity: Identity,
    target_department_id: str | None,
    *,
    audit_action: str,
    audit_details: dict,
    isolation_reason: str,
) -> str | None:
    """Определить department-scope записи или бросить DEPARTMENT_ISOLATION.

    Bot / oauth_client матрицу не правят — явный isolation-deny. account_admin
    пишет в любой переданный отдел. Department-bound caller — только в свой.
    """
    if identity.actor_type in {"bot", "oauth_client"}:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_subject_type": identity.actor_type,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Service accounts cannot manage entity_permissions",
            details={"target_department_id": target_department_id},
        )
    if _is_meta_admin(identity):
        return target_department_id
    actor_dept = identity.department_id
    if actor_dept is None:
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
            message="Caller has no department; cannot manage system-wide entity_permissions",
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
            message="Cannot manage entity_permissions for a different department",
            details={
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
    return actor_dept


def _reject_system_role(role: str, *, audit_action: str, audit_details: dict) -> None:
    """Отбить попытку править матрицу системной роли (`admin`/`guest`) — 409."""
    if not is_system_role(role):
        return
    audit_service.emit(
        audit_action,
        target_type="entity_permission",
        status="failure",
        allowed=True,
        details={**audit_details, "reason": "system_role_immutable"},
    )
    raise ConflictError(
        error_code="SYSTEM_ROLE_IMMUTABLE",
        message=f"System role '{role}' has a fixed permission matrix and cannot be modified",
        details={"role": role},
    )


# ── Catalog ──────────────────────────────────────────────────────────────────


def build_catalog() -> list[dict]:
    """Собрать каталог сущностей и действий из `ENTITY_ACTIONS` + описаний.

    Действия упорядочены по объявлению в `SecretAction`, чтобы порядок в выдаче
    был стабилен. Без авторизации — её делает `get_catalog`.
    """
    action_order = {a.value: i for i, a in enumerate(SecretAction)}
    catalog: list[dict] = []
    for entity_type in EntityType:
        actions = ENTITY_ACTIONS.get(entity_type, frozenset())
        catalog.append({
            "entity_type": entity_type.value,
            "description": ENTITY_DESCRIPTIONS[entity_type],
            "actions": [
                {
                    "action": action,
                    "description": ACTION_DESCRIPTIONS[action],
                    "sensitive": action in SENSITIVE_ACTIONS,
                }
                for action in sorted(actions, key=lambda a: action_order.get(a, 999))
            ],
        })
    return catalog


async def get_catalog(db: AsyncSession, identity: Identity) -> list[dict]:
    """Каталог сущностей и действий с описаниями. Read-only справочник для UI."""
    _require_matrix_access(identity)
    return build_catalog()


def describe_row(row: EntityPermission) -> dict:
    """Поля-обогащение строки матрицы описаниями каталога."""
    return {
        "entity_description": ENTITY_DESCRIPTIONS.get(row.entity_type, ""),
        "action_description": ACTION_DESCRIPTIONS.get(row.action, ""),
        "sensitive": row.action in SENSITIVE_ACTIONS,
    }


# ── List ─────────────────────────────────────────────────────────────────────


async def list_all(
    db: AsyncSession,
    identity: Identity,
    *,
    role: str | None = None,
) -> list[EntityPermission]:
    """Список grants. `role` сужает до одной роли. Scope — по видимости caller'а."""
    _require_matrix_access(identity)
    scope = _read_scope(identity)
    if role is not None:
        return await repo.list_for_role(db, role, department_id=scope)
    return await repo.list_all(db, department_id=scope)


async def list_for_entity(
    db: AsyncSession, identity: Identity, entity_type: str
) -> list[EntityPermission]:
    """Список grants одного entity_type. Неизвестный type → 422."""
    _require_matrix_access(identity)
    if entity_type not in {e.value for e in EntityType}:
        raise DomainValidationError(
            error_code="UNKNOWN_ENTITY_TYPE",
            message=f"Unknown entity_type '{entity_type}'",
        )
    return await repo.list_for_entity(db, entity_type, department_id=_read_scope(identity))


# ── Grant / Revoke ─────────────────────────────────────────────────────────────


async def grant_action(
    db: AsyncSession,
    identity: Identity,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> EntityPermission:
    """Выдать `action` на `entity_type` для `role`.

    Идемпотентно: повторный grant в тот же scope возвращает существующую строку
    без INSERT'а и без audit-emit.
    """
    _require_matrix_access(identity)
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    _reject_system_role(role, audit_action="permission.grant", audit_details=audit_details)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.grant",
        audit_details=audit_details,
        isolation_reason="department_isolation_grant",
    )
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
    existing = await repo.get(db, entity_type, role, action, department_id)
    if existing is not None:
        return existing
    try:
        obj = await repo.grant(
            db,
            permission_id=_new_permission_id(),
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
            message="Permission row already exists for this (entity_type, role, action, department_id)",
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
    identity: Identity,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> None:
    """Снять существующий grant. Отсутствие строки → 404 PERMISSION_NOT_FOUND."""
    _require_matrix_access(identity)
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    _reject_system_role(role, audit_action="permission.revoke", audit_details=audit_details)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.revoke",
        audit_details=audit_details,
        isolation_reason="department_isolation_revoke",
    )
    scope_details = {**audit_details, "department_id": department_id}
    removed = await repo.revoke(db, entity_type, role, action, department_id)
    if removed == 0:
        await db.rollback()
        audit_service.emit(
            "permission.revoke", target_type="entity_permission",
            status="failure", allowed=True,
            details={**scope_details, "reason": "not_found"},
        )
        raise NotFoundError(
            error_code="PERMISSION_NOT_FOUND",
            message="No permission row matched (entity_type, role, action, department_id)",
            details={
                "entity_type": entity_type,
                "role": role,
                "action": action,
                "department_id": department_id,
            },
        )
    await db.commit()
    audit_service.emit(
        "permission.revoke", target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
