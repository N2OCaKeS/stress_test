"""Department access management endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.departments import DepartmentCreate, DepartmentResponse, GrantServiceAccessRequest, ServiceAccessResponse
from src.services import department_service

router = APIRouter(prefix="/departments")


@router.get("", response_model=list[DepartmentResponse])
async def list_departments(
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[DepartmentResponse]:
    return await department_service.list_departments(
        db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("", response_model=DepartmentResponse, status_code=201)
async def create_department(
    body: DepartmentCreate,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    return await department_service.create_department(
        db=db,
        actor_id=identity.user_id,
        name=body.name,
        display_name=body.display_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{department_id}/services", response_model=ServiceAccessResponse, status_code=201)
async def grant_service_access(
    department_id: str,
    body: GrantServiceAccessRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> ServiceAccessResponse:
    return await department_service.grant_service_access(
        db=db,
        actor_id=identity.user_id,
        department_id=department_id,
        service_name=body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{department_id}/services/{service_name}", response_model=OkResponse)
async def revoke_service_access(
    department_id: str,
    service_name: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await department_service.revoke_service_access(
        db=db,
        actor_id=identity.user_id,
        department_id=department_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
