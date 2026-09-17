"""Use cases каталога СТП — тест-кейсы Zephyr Scale (§2.5 плана миграции).

Устройство зеркалит `test_definition.py`: чтение открыто любому
аутентифицированному актору, запись — под матрицей `(stp_test_case, *, ...)`.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.dependencies.auth import Identity
from src.models import StpTestCase
from src.repositories import stp_test_case as repo
from src.schemas.stp import StpTestCaseCreate, StpTestCaseUpdate
from src.services import audit_service, permissions
from src.utils.ids import stp_test_case_id as new_id

logger = logging.getLogger(__name__)


async def create_stp_test_case(
    db: AsyncSession, identity: Identity, payload: StpTestCaseCreate,
) -> StpTestCase:
    """INSERT нового тест-кейса СТП. UNIQUE(code) → 409 STP_TEST_CASE_DUPLICATE."""
    try:
        await permissions.require_action(db, identity, EntityType.STP_TEST_CASE, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "stp_test_case.create",
            target_type="stp_test_case",
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
        logger.warning("IntegrityError на создании stp_test_case: %s", type(exc.orig).__name__)
        audit_service.emit(
            "stp_test_case.create",
            target_type="stp_test_case",
            status="failure", allowed=True,
            details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="STP_TEST_CASE_DUPLICATE",
            message="Stp test case with this code already exists",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "stp_test_case.create",
        target_id=obj.id, target_type="stp_test_case",
        status="success", allowed=True,
        details={"code": obj.code, "zephyr_id": obj.zephyr_id},
    )
    return obj


async def get_stp_test_case(
    db: AsyncSession, identity: Identity, case_id: str,
) -> StpTestCase:
    obj = await repo.get_by_id(db, case_id)
    if obj is None:
        raise NotFoundError(error_code="STP_TEST_CASE_NOT_FOUND", message="Stp test case not found")
    permissions.require_own_department(identity, obj.department_id)
    return obj


async def list_stp_test_cases(
    db: AsyncSession, identity: Identity, limit: int, offset: int, *,
    department_id: str | None = None,
) -> tuple[list[StpTestCase], int]:
    """Каталог кейсов своего отдела + платформенные (`department_id IS NULL`)."""
    scope = permissions.own_department_or_403(identity, department_id)
    items = await repo.list_all(
        db, limit=limit, offset=offset, department_id=scope, include_unscoped=True,
    )
    total = await repo.count_all(db, department_id=scope, include_unscoped=True)
    return items, total


async def update_stp_test_case(
    db: AsyncSession, identity: Identity, case_id: str, payload: StpTestCaseUpdate,
) -> StpTestCase:
    try:
        await permissions.require_action(db, identity, EntityType.STP_TEST_CASE, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "stp_test_case.update",
            target_id=case_id, target_type="stp_test_case",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, case_id)
    if obj is None:
        raise NotFoundError(error_code="STP_TEST_CASE_NOT_FOUND", message="Stp test case not found")

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "stp_test_case.update",
            target_id=case_id, target_type="stp_test_case",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="STP_TEST_CASE_DUPLICATE",
            message="Update collides with an existing stp test case (code UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "stp_test_case.update",
        target_id=obj.id, target_type="stp_test_case",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return obj


async def delete_stp_test_case(db: AsyncSession, identity: Identity, case_id: str) -> None:
    try:
        await permissions.require_action(db, identity, EntityType.STP_TEST_CASE, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "stp_test_case.delete",
            target_id=case_id, target_type="stp_test_case",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, case_id)
    if obj is None:
        raise NotFoundError(error_code="STP_TEST_CASE_NOT_FOUND", message="Stp test case not found")
    code = obj.code
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "stp_test_case.delete",
        target_id=case_id, target_type="stp_test_case",
        status="success", allowed=True,
        details={"code": code},
    )
