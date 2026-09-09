"""Use cases каталога тестов (§2.2 плана миграции).

Устройство зеркалит `global_variable.py`: чтение (list / карточка по id)
открыто любому аутентифицированному актору, запись (create/update/delete)
идёт под матрицей прав `(test_definition, *, ...)`. В отличие от глобальных
переменных тест несёт `department_id`, но сам грант на запись — по-прежнему
system-wide роль `admin` (§16 волна 3 плана миграции); per-department гранты
кастомным ролям появятся вместе с администрированием отдела.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestDefinition
from src.repositories import test_definition as repo
from src.schemas.test_definition import TestDefinitionCreate, TestDefinitionUpdate
from src.services import audit_service, permissions
from src.utils.ids import test_definition_id as new_id

logger = logging.getLogger(__name__)


async def create_test_definition(
    db: AsyncSession,
    identity: Identity,
    payload: TestDefinitionCreate,
) -> TestDefinition:
    """INSERT нового теста. UNIQUE(code) → 409 TEST_DEFINITION_DUPLICATE."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.create",
            target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на создании test_definition: %s", type(exc.orig).__name__
        )
        audit_service.emit(
            "test_definition.create",
            target_type="test_definition",
            status="failure", allowed=True,
            details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="TEST_DEFINITION_DUPLICATE",
            message="Test definition with this code already exists",
            details={"hint": "уникальное поле — code"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "test_definition.create",
        target_id=obj.id, target_type="test_definition",
        status="success", allowed=True,
        details={"code": obj.code, "department_id": obj.department_id},
    )
    return obj


async def get_test_definition(db: AsyncSession, test_id: str) -> TestDefinition:
    """SELECT теста по PK. Read без проверки прав и без аудита."""
    obj = await repo.get_by_id(db, test_id)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    return obj


async def get_test_definition_by_code(db: AsyncSession, code: str) -> TestDefinition:
    """SELECT теста по UNIQUE code. Read без проверки прав и без аудита."""
    obj = await repo.get_by_code(db, code)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    return obj


async def list_test_definitions(
    db: AsyncSession,
    limit: int,
    offset: int,
    *,
    department_id: str | None = None,
    category: str | None = None,
    readiness: str | None = None,
) -> tuple[list[TestDefinition], int]:
    """List + count каталога под фильтрами. Read без проверки прав и без аудита."""
    items = await repo.list_all(
        db, limit=limit, offset=offset,
        department_id=department_id, category=category, readiness=readiness,
    )
    total = await repo.count_all(
        db, department_id=department_id, category=category, readiness=readiness,
    )
    return items, total


async def update_test_definition(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    payload: TestDefinitionUpdate,
) -> TestDefinition:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.update",
            target_id=test_id, target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, test_id)
    if obj is None:
        audit_service.emit(
            "test_definition.update",
            target_id=test_id, target_type="test_definition",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на обновлении test_definition %s: %s",
            test_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "test_definition.update",
            target_id=test_id, target_type="test_definition",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="TEST_DEFINITION_DUPLICATE",
            message="Update collides with an existing test definition (code UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "test_definition.update",
        target_id=obj.id, target_type="test_definition",
        status="success", allowed=True,
        details={"fields": list(changes.keys()), "code": obj.code},
    )
    return obj


async def delete_test_definition(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
) -> None:
    """Hard-delete теста. Каскадом сносит его test_command_args."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.delete",
            target_id=test_id, target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, test_id)
    if obj is None:
        audit_service.emit(
            "test_definition.delete",
            target_id=test_id, target_type="test_definition",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    code = obj.code
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "test_definition.delete",
        target_id=test_id, target_type="test_definition",
        status="success", allowed=True,
        details={"code": code},
    )
