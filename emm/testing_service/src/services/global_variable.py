"""Use cases каталога глобальных переменных — платформенный справочник.

Устройство зеркалит `server_category`/`os_version` в server_service: чтение
(list / карточка по id / карточка по коду / резолв choices) открыто любому
аутентифицированному актору и не аудитится, запись (create/update/delete)
идёт под матрицей прав `(global_variable, *, ...)`.

Переменные платформенные, не per-department (§10 плана миграции): один
каталог на всю платформу, department-скоуп появляется уровнем выше — в
тестах, стендах и настройках отдела.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import GlobalVariable
from src.repositories import global_variable as repo
from src.schemas.global_variable import GlobalVariableCreate, GlobalVariableUpdate
from src.services import audit_service, choices, permissions
from src.utils.ids import global_variable_id as new_id

logger = logging.getLogger(__name__)


def _validate_choices_source(value: str | None) -> None:
    """Отбить источник, который сервис не умеет резолвить.

    Содержимое `static:` тут не разбирается — это данные, они парсятся при
    резолве. Проверяется только форма строки и наличие резолвера.
    """
    if value is None or not value.strip():
        return
    if not choices.is_supported(value.strip()):
        raise DomainValidationError(
            error_code="CHOICES_SOURCE_INVALID",
            message=(
                "choices_source must be 'static:<json>' or 'dynamic:<resolver>' "
                "with a known resolver"
            ),
            details={"known_resolvers": sorted(choices.RESOLVERS)},
        )


async def create_global_variable(
    db: AsyncSession,
    identity: Identity,
    payload: GlobalVariableCreate,
) -> GlobalVariable:
    """INSERT новой переменной. UNIQUE(code) → 409 GLOBAL_VARIABLE_DUPLICATE."""
    try:
        await permissions.require_action(
            db, identity, EntityType.GLOBAL_VARIABLE, Action.CREATE
        )
    except AuthorizationError:
        audit_service.emit(
            "global_variable.create",
            target_type="global_variable",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    _validate_choices_source(payload.choices_source)

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на создании global_variable: %s", type(exc.orig).__name__
        )
        audit_service.emit(
            "global_variable.create",
            target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="GLOBAL_VARIABLE_DUPLICATE",
            message="Global variable with this code already exists",
            details={"hint": "уникальное поле — code"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "global_variable.create",
        target_id=obj.id, target_type="global_variable",
        status="success", allowed=True,
        details={"code": obj.code, "source": obj.source, "is_sensitive": obj.is_sensitive},
    )
    return obj


async def get_global_variable(db: AsyncSession, variable_id: str) -> GlobalVariable:
    """SELECT переменной по PK. Read без проверки прав и без аудита."""
    obj = await repo.get_by_id(db, variable_id)
    if obj is None:
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
        )
    return obj


async def get_global_variable_by_code(db: AsyncSession, code: str) -> GlobalVariable:
    """SELECT переменной по UNIQUE code. Read без проверки прав и без аудита."""
    obj = await repo.get_by_code(db, code)
    if obj is None:
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
        )
    return obj


async def list_global_variables(
    db: AsyncSession, limit: int, offset: int,
) -> tuple[list[GlobalVariable], int]:
    """List + count каталога. Read без проверки прав и без аудита."""
    items = await repo.list_all(db, limit=limit, offset=offset)
    total = await repo.count_all(db)
    return items, total


async def update_global_variable(
    db: AsyncSession,
    identity: Identity,
    variable_id: str,
    payload: GlobalVariableUpdate,
) -> GlobalVariable:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(
            db, identity, EntityType.GLOBAL_VARIABLE, Action.UPDATE
        )
    except AuthorizationError:
        audit_service.emit(
            "global_variable.update",
            target_id=variable_id, target_type="global_variable",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, variable_id)
    if obj is None:
        audit_service.emit(
            "global_variable.update",
            target_id=variable_id, target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    if "choices_source" in changes:
        _validate_choices_source(changes["choices_source"])
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на обновлении global_variable %s: %s",
            variable_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "global_variable.update",
            target_id=variable_id, target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="GLOBAL_VARIABLE_DUPLICATE",
            message="Update collides with an existing global variable (code UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "global_variable.update",
        target_id=obj.id, target_type="global_variable",
        status="success", allowed=True,
        details={"fields": list(changes.keys()), "code": obj.code},
    )
    return obj


async def delete_global_variable(
    db: AsyncSession,
    identity: Identity,
    variable_id: str,
) -> None:
    """Hard-delete переменной каталога."""
    try:
        await permissions.require_action(
            db, identity, EntityType.GLOBAL_VARIABLE, Action.DELETE
        )
    except AuthorizationError:
        audit_service.emit(
            "global_variable.delete",
            target_id=variable_id, target_type="global_variable",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, variable_id)
    if obj is None:
        audit_service.emit(
            "global_variable.delete",
            target_id=variable_id, target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
        )
    code = obj.code
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "global_variable.delete",
        target_id=variable_id, target_type="global_variable",
        status="success", allowed=True,
        details={"code": code},
    )


async def resolve_choices(
    db: AsyncSession,
    variable_id: str,
    params: dict[str, str],
) -> tuple[list[dict], str]:
    """Резолв `choices_source` переменной в список значений.

    Зовётся в момент отображения списка в UI, а не при сохранении переменной,
    поэтому список всегда актуален: новый РЦ появляется в выпадашке сам, без
    правки каталога.
    """
    obj = await get_global_variable(db, variable_id)
    source = (obj.choices_source or "").strip()
    if not source:
        raise DomainValidationError(
            error_code="CHOICES_SOURCE_NOT_SET",
            message="Global variable has no choices_source — value is free-form",
            details={"code": obj.code},
        )
    items = await choices.resolve(source, params)
    return items, source
