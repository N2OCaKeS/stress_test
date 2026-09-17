"""Use cases сотрудников отдела для HR-отчёта по активности (§9.1 плана миграции).

Ростер — персональные данные отдела (ФИО, Jira-аккаунты), поэтому и чтение, и
запись department-scoped. Чтение — `permissions.require_own_department`
(любая роль своего отдела, чужой отдел — 403 `DEPARTMENT_ISOLATION`), запись —
`require_department_action` на `(department_report_member, *,
create|update|delete)`.

`department_id` в URL задаёт отдел только для create; update/delete берут его
с самой строки (`member_id` глобально уникален, URL-сегмент подставляет
клиент).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import DepartmentReportMember
from src.repositories import department_report_member as repo
from src.schemas.department_report_member import (
    DepartmentReportMemberCreate,
    DepartmentReportMemberUpdate,
)
from src.services import audit_service, permissions
from src.utils.ids import department_report_member_id as new_id


async def list_members(
    db: AsyncSession, identity: Identity, department_id: str, *, limit: int, offset: int,
    is_active: bool | None = None,
) -> tuple[list[DepartmentReportMember], int]:
    permissions.require_own_department(identity, department_id)
    items = await repo.list_by_department(db, department_id, limit=limit, offset=offset, is_active=is_active)
    total = await repo.count_by_department(db, department_id, is_active=is_active)
    return items, total


async def get_member(db: AsyncSession, member_id: str) -> DepartmentReportMember:
    """SELECT по PK без гейта — внутренний примитив для write-путей этого модуля."""
    obj = await repo.get_by_id(db, member_id)
    if obj is None:
        raise NotFoundError(
            error_code="DEPARTMENT_REPORT_MEMBER_NOT_FOUND", message="Department report member not found",
        )
    return obj


async def view_member(
    db: AsyncSession, identity: Identity, member_id: str,
) -> DepartmentReportMember:
    """Карточка сотрудника для HTTP-чтения — отдел берётся со строки, не из URL."""
    obj = await get_member(db, member_id)
    permissions.require_own_department(identity, obj.department_id)
    return obj


async def create_member(
    db: AsyncSession, identity: Identity, department_id: str, payload: DepartmentReportMemberCreate,
) -> DepartmentReportMember:
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.DEPARTMENT_REPORT_MEMBER, Action.CREATE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_report_member.create",
            target_type="department_report_member",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": department_id},
        )
        raise

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["department_id"] = department_id
    data["created_by"] = identity.user_id
    obj = await repo.create(db, data)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "department_report_member.create",
        target_id=obj.id, target_type="department_report_member",
        status="success", allowed=True,
        details={"department_id": department_id, "display_name": obj.display_name},
    )
    return obj


async def update_member(
    db: AsyncSession, identity: Identity, member_id: str, payload: DepartmentReportMemberUpdate,
) -> DepartmentReportMember:
    # Строка читается ДО авторизации: отдел берётся с неё самой, а не из URL.
    # `member_id` глобально уникален, роутер смонтирован под
    # `/departments/{department_id}/...` — доверять этому сегменту нельзя,
    # его подставляет клиент.
    obj = await get_member(db, member_id)
    try:
        await permissions.require_department_action(
            db, identity, obj.department_id, EntityType.DEPARTMENT_REPORT_MEMBER, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_report_member.update",
            target_id=member_id, target_type="department_report_member",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": obj.department_id},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if changes:
        await repo.update(db, obj, changes)
        await db.commit()
        await db.refresh(obj)
    audit_service.emit(
        "department_report_member.update",
        target_id=obj.id, target_type="department_report_member",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return obj


async def delete_member(db: AsyncSession, identity: Identity, member_id: str) -> None:
    # Как и в update_member — сначала строка, потом авторизация по её отделу.
    obj = await get_member(db, member_id)
    try:
        await permissions.require_department_action(
            db, identity, obj.department_id, EntityType.DEPARTMENT_REPORT_MEMBER, Action.DELETE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_report_member.delete",
            target_id=member_id, target_type="department_report_member",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": obj.department_id},
        )
        raise

    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "department_report_member.delete",
        target_id=member_id, target_type="department_report_member",
        status="success", allowed=True,
        details={"department_id": obj.department_id},
    )
