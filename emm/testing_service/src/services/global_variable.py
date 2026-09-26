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

from src.core.constants import Action, EntityType, GlobalVariableSource
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import GlobalVariable, TestStand
from src.repositories import global_variable as repo
from src.schemas.global_variable import GlobalVariableCreate, GlobalVariableUpdate
from src.services import audit_service, choices, permissions, variable_resolver
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


async def _validate_definition(
    db: AsyncSession,
    *,
    code: str,
    source: str,
    source_ref: dict | None,
    is_sensitive: bool,
    original_code: str | None = None,
) -> dict | None:
    """`source_ref` под `source` + целостность шаблонов каталога.

    Возвращает нормализованный `source_ref` (`{}` → `None` у источников без
    ссылки).
    """
    normalized = variable_resolver.validate_source_ref(source, source_ref, is_sensitive=is_sensitive)
    if source == GlobalVariableSource.STAND_REF and await db.get(TestStand, normalized["stand_id"]) is None:
        raise DomainValidationError(
            error_code="VARIABLE_SOURCE_REF_INVALID",
            message=f"Stand '{normalized['stand_id']}' does not exist",
            details={"source": source, "stand_id": normalized["stand_id"]},
        )
    await variable_resolver.validate_catalog_change(
        db, code=code, source=source, source_ref=normalized, original_code=original_code,
    )
    return normalized


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
    try:
        data["source_ref"] = await _validate_definition(
            db, code=payload.code, source=payload.source, source_ref=payload.source_ref,
            is_sensitive=payload.is_sensitive,
        )
    except DomainValidationError as exc:
        audit_service.emit(
            "global_variable.create",
            target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": exc.error_code, "code": payload.code},
        )
        raise
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
    if changes.keys() & {"code", "source", "source_ref", "is_sensitive"}:
        try:
            normalized = await _validate_definition(
                db,
                code=changes.get("code") or obj.code,
                source=changes.get("source") or obj.source,
                source_ref=changes["source_ref"] if "source_ref" in changes else obj.source_ref,
                is_sensitive=changes["is_sensitive"] if changes.get("is_sensitive") is not None else obj.is_sensitive,
                original_code=obj.code,
            )
        except (DomainValidationError, ConflictError) as exc:
            audit_service.emit(
                "global_variable.update",
                target_id=variable_id, target_type="global_variable",
                status="failure", allowed=True,
                details={"reason": exc.error_code, "fields": list(changes.keys())},
            )
            raise
        if "source_ref" in changes or "source" in changes:
            changes["source_ref"] = normalized
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
    try:
        # FK слотов ловит ссылку `variable_id`, но не упоминание `{CODE}` в
        # шаблонах других переменных и в `override_value` — их проверяем сами.
        await variable_resolver.ensure_not_referenced(db, code, action="delete")
    except ConflictError:
        audit_service.emit(
            "global_variable.delete",
            target_id=variable_id, target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "in_use", "code": code},
        )
        raise
    try:
        await repo.delete(db, obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на удалении global_variable %s: %s",
            variable_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "global_variable.delete",
            target_id=variable_id, target_type="global_variable",
            status="failure", allowed=True,
            details={"reason": "in_use", "code": code},
        )
        raise ConflictError(
            error_code="GLOBAL_VARIABLE_IN_USE",
            message="Global variable is referenced by test command args and cannot be deleted",
            details={"hint": "удалите или переключите ссылающиеся слоты теста"},
        ) from exc
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


def source_options() -> dict:
    """Допустимые значения `source_ref` по источникам — для формы переменной в UI."""
    from src.core.constants import GlobalVariableSource

    vr = variable_resolver
    return {
        "sources": [s.value for s in GlobalVariableSource],
        "test_fields": sorted(vr.TEST_FIELDS),
        "stand_fields": sorted(vr.STAND_FIELDS),
        "stand_ref_fields": sorted(vr.STAND_REF_FIELDS),
        "department_integration_fields": [
            {"field": name, "is_credential": vr.is_credential_field(name)}
            for name in sorted(vr.DEPARTMENT_INTEGRATION_FIELDS)
        ],
        "credential_parts": sorted(vr.CREDENTIAL_PARTS),
        "os_version_fields": sorted(vr.OS_VERSION_FIELDS),
        "test_account_fields": sorted(vr.TEST_ACCOUNT_FIELDS),
        "zephyr_folder_fields": sorted(vr.ZEPHYR_FOLDER_FIELDS),
        "template_conditions": sorted(vr.TEMPLATE_CONDITIONS),
    }
