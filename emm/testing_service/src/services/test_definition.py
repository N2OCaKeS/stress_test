"""Use cases каталога тестов (§2.2 плана миграции).

Устройство зеркалит `global_variable.py`: чтение (list / карточка по id)
открыто любому аутентифицированному актору, запись (create/update/delete)
идёт под матрицей прав `(test_definition, *, ...)`. В отличие от глобальных
переменных тест несёт `department_id`, но сам грант на запись — по-прежнему
system-wide роль `admin` (§16 волна 3 плана миграции); per-department гранты
кастомным ролям появятся вместе с администрированием отдела.

`department_id` — nullable: `NULL` значит платформенный тест каталога (так
заводит импорт легаси), непустой — тест конкретного отдела. Для платформенных
строк матрицы достаточно (любой `admin` правит общий каталог, как и раньше);
для department-строк write дополнительно гейтится `require_department_action`
по фактическому `department_id` — своей строки (create/update reassignment)
или чужой (update/delete существующей). Без этого `admin` отдела A мог бы
менять/удалять тест отдела B по id, либо завести/увести тест «от имени»
чужого отдела через поле в теле запроса.
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
    """INSERT нового теста. UNIQUE(code) → 409 TEST_DEFINITION_DUPLICATE.

    `department_id` в теле — либо пусто (платформенный тест, обычная матрица),
    либо СВОЙ отдел caller'а: чужой id в этом поле отклоняет
    `require_department_action`, иначе можно было бы завести тест «от имени»
    другого отдела, просто подставив его id в JSON.
    """
    target_department_id = payload.department_id
    try:
        if target_department_id is not None:
            await permissions.require_department_action(
                db, identity, target_department_id, EntityType.TEST_DEFINITION, Action.CREATE,
            )
        else:
            await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.create",
            target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": target_department_id},
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


async def get_test_definition(
    db: AsyncSession, identity: Identity, test_id: str,
) -> TestDefinition:
    """SELECT теста по PK. Без ролевой проверки и аудита, но в пределах своего отдела."""
    obj = await repo.get_by_id(db, test_id)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    permissions.require_own_department(identity, obj.department_id)
    return obj


async def get_test_definition_by_code(
    db: AsyncSession, identity: Identity, code: str,
) -> TestDefinition:
    """SELECT теста по UNIQUE code. Тот же скоуп, что и у чтения по id."""
    obj = await repo.get_by_code(db, code)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    permissions.require_own_department(identity, obj.department_id)
    return obj


async def list_test_definitions(
    db: AsyncSession,
    identity: Identity,
    limit: int,
    offset: int,
    *,
    department_id: str | None = None,
    category: str | None = None,
    readiness: str | None = None,
) -> tuple[list[TestDefinition], int]:
    """List + count каталога под фильтрами, суженными до отдела caller'а.

    `department_id` остаётся параметром запроса, но чужой отдел в нём — отказ,
    а не выдача: дефолт «все отделы» здесь означал бы отсутствие изоляции.
    Платформенные тесты (`department_id IS NULL`) видны всем — см.
    `permissions.require_own_department`.
    """
    scope = permissions.own_department_or_403(identity, department_id)
    items = await repo.list_all(
        db, limit=limit, offset=offset,
        department_id=scope, category=category, readiness=readiness,
        include_unscoped=True,
    )
    total = await repo.count_all(
        db, department_id=scope, category=category, readiness=readiness,
        include_unscoped=True,
    )
    return items, total


async def update_test_definition(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    payload: TestDefinitionUpdate,
) -> TestDefinition:
    """PATCH-обновление. Пустой диф → возврат без UPDATE.

    Строка читается ДО авторизации: платформенный тест (`department_id IS
    NULL`) по-прежнему правит любой носитель `admin` (обычная матрица),
    department-тест — только `require_department_action` по его фактическому
    отделу, не по роли самой по себе. Смена `department_id` в теле проходит
    тот же гейт над НОВЫМ значением — иначе admin своего отдела мог бы просто
    переписать поле и увести тест в чужой.
    """
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

    try:
        if obj.department_id is not None:
            await permissions.require_department_action(
                db, identity, obj.department_id, EntityType.TEST_DEFINITION, Action.UPDATE,
            )
        else:
            await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.update",
            target_id=test_id, target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": obj.department_id},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj

    if "department_id" in changes and changes["department_id"] != obj.department_id:
        new_department_id = changes["department_id"]
        try:
            if new_department_id is not None:
                await permissions.require_department_action(
                    db, identity, new_department_id, EntityType.TEST_DEFINITION, Action.UPDATE,
                )
        except AuthorizationError:
            audit_service.emit(
                "test_definition.update",
                target_id=test_id, target_type="test_definition",
                status="denied", allowed=False,
                details={"reason": "permission_denied", "department_id": new_department_id},
            )
            raise

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
    """Hard-delete теста. Каскадом сносит его test_command_args.

    Строка читается ДО авторизации — тот же приём, что в `update_test_definition`.
    """
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
    try:
        if obj.department_id is not None:
            await permissions.require_department_action(
                db, identity, obj.department_id, EntityType.TEST_DEFINITION, Action.DELETE,
            )
        else:
            await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "test_definition.delete",
            target_id=test_id, target_type="test_definition",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": obj.department_id},
        )
        raise
    code = obj.code
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "test_definition.delete",
        target_id=test_id, target_type="test_definition",
        status="success", allowed=True,
        details={"code": code},
    )
