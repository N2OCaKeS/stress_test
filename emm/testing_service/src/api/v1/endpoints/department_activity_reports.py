"""`/departments/{department_id}/activity-reports` (§2.7, §9.1 плана миграции).

Ручная генерация HR-отчёта по активности + история прошлых генераций.
department-scoped операция над бизнес-данными конкретного отдела — RBAC
через `permissions.require_department_action` внутри сервиса (department_admin
своего отдела ИЛИ носитель `admin` service-роли `testing_service` в этом же
отделе), не открытое чтение, как у платформенных каталогов этого сервиса.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import PaginatedResponse
from src.schemas.department_activity_report import (
    DepartmentActivityReportGenerateRequest,
    DepartmentActivityReportResponse,
)
from src.services import activity_report as svc

router = APIRouter(prefix="/departments/{department_id}/activity-reports")


@router.post(
    "/generate",
    response_model=DepartmentActivityReportResponse,
    status_code=201,
    summary="Сгенерировать HR-отчёт по активности отдела за период",
    description=(
        "Замена легаси-паттерну ручной правки MONTH в коде и перезапуска скрипта. "
        "Частичный провал одного источника (Bitbucket/Jira/Tempo) не рушит отчёт — "
        "публикуется с нулями по недоступному источнику, причина видна в `error`."
    ),
    responses={
        403: {"description": "Каller не department_admin/admin этого отдела."},
        422: {"description": "Отдел не настроил confluence_report_page_space/confluence_base_url."},
    },
)
async def generate_activity_report(
    department_id: str,
    body: DepartmentActivityReportGenerateRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentActivityReportResponse:
    obj = await svc.generate_report(db, identity, department_id, body.period)
    return DepartmentActivityReportResponse.model_validate(obj)


@router.get(
    "",
    response_model=PaginatedResponse[DepartmentActivityReportResponse],
    summary="История генераций HR-отчёта отдела",
    responses={403: {"description": "Каller не department_admin/admin этого отдела."}},
)
async def list_activity_reports(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[DepartmentActivityReportResponse]:
    items, total = await svc.list_reports(db, identity, department_id, limit=limit, offset=offset)
    return PaginatedResponse[DepartmentActivityReportResponse](
        items=[DepartmentActivityReportResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )
