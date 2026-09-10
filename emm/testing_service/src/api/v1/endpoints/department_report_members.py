"""CRUD `/departments/{department_id}/report-members` (§9.1 плана миграции).

Список сотрудников отдела, учитываемых HR-отчётом по активности. Чтение
открыто любому аутентифицированному актору, запись — под матрицей
`(department_report_member, *, create|update|delete)`.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.department_report_member import (
    DepartmentReportMemberCreate,
    DepartmentReportMemberResponse,
    DepartmentReportMemberUpdate,
)
from src.services import department_report_member as svc

router = APIRouter(prefix="/departments/{department_id}/report-members")


@router.get(
    "",
    response_model=PaginatedResponse[DepartmentReportMemberResponse],
    summary="Список сотрудников отдела для HR-отчёта",
)
async def list_report_members(
    department_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    is_active: bool | None = Query(default=None),
) -> PaginatedResponse[DepartmentReportMemberResponse]:
    items, total = await svc.list_members(db, department_id, limit=limit, offset=offset, is_active=is_active)
    return PaginatedResponse[DepartmentReportMemberResponse](
        items=[DepartmentReportMemberResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post(
    "",
    response_model=DepartmentReportMemberResponse,
    status_code=201,
    summary="Добавить сотрудника отдела в HR-отчёт",
    responses={403: {"description": "Нет роли с `create`."}},
)
async def create_report_member(
    department_id: str,
    body: DepartmentReportMemberCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentReportMemberResponse:
    obj = await svc.create_member(db, identity, department_id, body)
    return DepartmentReportMemberResponse.model_validate(obj)


@router.get(
    "/{member_id}",
    response_model=DepartmentReportMemberResponse,
    summary="Карточка сотрудника отдела",
    responses={404: {"description": "Не найден."}},
)
async def get_report_member(
    department_id: str, member_id: str, identity: AuthenticatedIdentity, db: AsyncSession = Depends(get_db),
) -> DepartmentReportMemberResponse:
    obj = await svc.get_member(db, member_id)
    return DepartmentReportMemberResponse.model_validate(obj)


@router.patch(
    "/{member_id}",
    response_model=DepartmentReportMemberResponse,
    summary="Обновить сотрудника отдела",
    responses={403: {"description": "Нет `update`."}, 404: {"description": "Не найден."}},
)
async def update_report_member(
    department_id: str,
    member_id: str,
    body: DepartmentReportMemberUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentReportMemberResponse:
    obj = await svc.update_member(db, identity, member_id, body)
    return DepartmentReportMemberResponse.model_validate(obj)


@router.delete(
    "/{member_id}",
    response_model=OkResponse,
    summary="Удалить сотрудника отдела из HR-отчёта",
    responses={403: {"description": "Нет `delete`."}, 404: {"description": "Не найден."}},
)
async def delete_report_member(
    department_id: str, member_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await svc.delete_member(db, identity, member_id)
    return OkResponse()
