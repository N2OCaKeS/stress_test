"""HTTP-эндпоинты /credentials/{id}/dept-grants — только cross_department."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.limiter import limiter
from src.dependencies.auth import CurrentIdentity, require_user_context
from src.dependencies.db import get_db
from src.models import DeptGrant
from src.schemas.common import OkResponse
from src.schemas.dept_grants import DeptGrantCreate, DeptGrantList, DeptGrantRead
from src.services import dept_grant_service

router = APIRouter(prefix="/credentials", tags=["dept_grants"])


def _to_read(g: DeptGrant) -> DeptGrantRead:
    return DeptGrantRead(
        id=g.id,
        cred_id=g.cred_id,
        recipient_dept_id=g.recipient_dept_id,
        granted_by_user_id=g.granted_by_user_id,
        granted_at=g.granted_at,
    )


@router.post(
    "/{cred_id}/dept-grants",
    response_model=DeptGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать DeptGrant recipient-dep'у (owner dep_admin / service_admin)",
)
@limiter.limit(get_settings().rate_limit_dept_grant)
async def add_dept_grant(
    cred_id: str,
    request: Request,
    payload: DeptGrantCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> DeptGrantRead:
    require_user_context(identity)
    grant = await dept_grant_service.add(db, identity, cred_id, payload)
    return _to_read(grant)


@router.get(
    "/{cred_id}/dept-grants",
    response_model=DeptGrantList,
    summary="Список DeptGrant'ов для cross_department credential",
)
async def list_dept_grants(
    cred_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> DeptGrantList:
    require_user_context(identity)
    grants = await dept_grant_service.list_for(db, identity, cred_id)
    return DeptGrantList(items=[_to_read(g) for g in grants])


@router.delete(
    "/{cred_id}/dept-grants/{grant_id}",
    response_model=OkResponse,
    summary="Снять DeptGrant (cascade RoleACL для recipient_dep)",
)
async def revoke_dept_grant(
    cred_id: str,
    grant_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    require_user_context(identity)
    await dept_grant_service.revoke(db, identity, cred_id, grant_id)
    return OkResponse(ok=True)
