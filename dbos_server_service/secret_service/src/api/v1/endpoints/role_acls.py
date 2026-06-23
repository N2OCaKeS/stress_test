"""HTTP-эндпоинты /credentials/{id}/acl — RoleACL CRUD."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.limiter import limiter
from src.dependencies.auth import CurrentIdentity, require_user_context
from src.dependencies.db import get_db
from src.models import RoleACL
from src.schemas.common import OkResponse
from src.schemas.role_acls import (
    RoleACLCreate,
    RoleACLList,
    RoleACLRead,
    RoleACLUpsert,
    RoleACLUpsertResponse,
)
from src.services import role_acl_service

router = APIRouter(prefix="/credentials", tags=["role_acls"])


def _to_read(acl: RoleACL) -> RoleACLRead:
    return RoleACLRead(
        id=acl.id,
        cred_id=acl.cred_id,
        dept_id=acl.dept_id,
        role_name=acl.role_name,
        can_read=acl.can_read,
        can_write=acl.can_write,
        granted_by_user_id=acl.granted_by_user_id,
        granted_at=acl.granted_at,
    )


@router.post(
    "/{cred_id}/acl",
    response_model=RoleACLRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать RoleACL (cross_department recipient требует DeptGrant)",
)
@limiter.limit(get_settings().rate_limit_acl)
async def add_acl(
    cred_id: str,
    request: Request,
    payload: RoleACLCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> RoleACLRead:
    require_user_context(identity)
    acl = await role_acl_service.add(db, identity, cred_id, payload)
    return _to_read(acl)


@router.put(
    "/{cred_id}/acl",
    response_model=RoleACLUpsertResponse,
    summary="Upsert RoleACL: задать пару (can_read, can_write) для (dept, role)",
)
@limiter.limit(get_settings().rate_limit_acl)
async def upsert_acl(
    cred_id: str,
    request: Request,
    payload: RoleACLUpsert,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> RoleACLUpsertResponse:
    require_user_context(identity)
    acl = await role_acl_service.upsert(db, identity, cred_id, payload)
    return RoleACLUpsertResponse(ok=True, acl=_to_read(acl) if acl else None)


@router.get(
    "/{cred_id}/acl",
    response_model=RoleACLList,
    summary="Список RoleACL для credential",
)
async def list_acls(
    cred_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> RoleACLList:
    require_user_context(identity)
    acls = await role_acl_service.list_for(db, identity, cred_id)
    return RoleACLList(items=[_to_read(a) for a in acls])


@router.delete(
    "/{cred_id}/acl/{acl_id}",
    response_model=OkResponse,
    summary="Снять RoleACL",
)
async def revoke_acl(
    cred_id: str,
    acl_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    require_user_context(identity)
    await role_acl_service.revoke(db, identity, cred_id, acl_id)
    return OkResponse(ok=True)
