"""Service role definition endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from pydantic import BaseModel

from src.schemas.common import OkResponse
from src.schemas.service_roles import ServiceRoleCreate, ServiceRoleResponse, ServiceRoleUpdate
from src.services import service_role_service


class BulkRoleRequest(BaseModel):
    user_ids: list[str]

router = APIRouter(prefix="/services/{service_name}/roles")


@router.get("", response_model=list[ServiceRoleResponse])
async def list_roles(
    service_name: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[ServiceRoleResponse]:
    return await service_role_service.list_roles(
        db=db,
        identity=identity,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("", response_model=ServiceRoleResponse, status_code=201)
async def create_role(
    service_name: str,
    body: ServiceRoleCreate,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServiceRoleResponse:
    return await service_role_service.create_role(
        db=db,
        identity=identity,
        service_name=service_name,
        role_name=body.role_name,
        display_name=body.display_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{role_name}", response_model=ServiceRoleResponse)
async def update_role(
    service_name: str,
    role_name: str,
    body: ServiceRoleUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServiceRoleResponse:
    return await service_role_service.update_role(
        db=db,
        identity=identity,
        service_name=service_name,
        role_name=role_name,
        display_name=body.display_name,
        description=body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{role_name}", response_model=OkResponse)
async def delete_role(
    service_name: str,
    role_name: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await service_role_service.delete_role(
        db=db,
        identity=identity,
        service_name=service_name,
        role_name=role_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post("/{role_name}/assign", response_model=OkResponse)
async def bulk_assign(
    service_name: str,
    role_name: str,
    body: BulkRoleRequest,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await service_role_service.bulk_assign(
        db=db,
        identity=identity,
        service_name=service_name,
        role_name=role_name,
        user_ids=body.user_ids,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post("/{role_name}/revoke", response_model=OkResponse)
async def bulk_revoke(
    service_name: str,
    role_name: str,
    body: BulkRoleRequest,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await service_role_service.bulk_revoke(
        db=db,
        identity=identity,
        service_name=service_name,
        role_name=role_name,
        user_ids=body.user_ids,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
