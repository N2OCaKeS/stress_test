"""Token introspection and service access endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.schemas.authorization import IntrospectRequest, IntrospectResponse, ServiceAccessRequest, ServiceAccessResponse
from src.services import authorization_service

router = APIRouter(prefix="/authorization")


@router.post("/introspect", response_model=IntrospectResponse)
async def introspect(
    body: IntrospectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IntrospectResponse:
    return await authorization_service.introspect(
        db=db,
        token=body.token,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/service-access", response_model=ServiceAccessResponse)
async def check_service_access(
    body: ServiceAccessRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServiceAccessResponse:
    return await authorization_service.check_service_access(
        db=db,
        token=body.subject_token,
        service_name=body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )
