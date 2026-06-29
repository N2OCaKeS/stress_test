"""HTTP-эндпоинты /credentials/{id}/user-acl — CredentialUserACL CRUD."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.limiter import limiter
from src.dependencies.auth import CurrentIdentity, require_user_context
from src.dependencies.db import get_db
from src.models import CredentialUserACL
from src.schemas.common import OkResponse
from src.schemas.user_acls import UserACLCreate, UserACLList, UserACLOut
from src.services import user_acl_service

router = APIRouter(prefix="/credentials", tags=["user_acls"])


def _to_out(acl: CredentialUserACL) -> UserACLOut:
    return UserACLOut(
        id=acl.id,
        cred_id=acl.cred_id,
        user_id=acl.user_id,
        can_view=acl.can_view,
        can_read=acl.can_read,
        can_write=acl.can_write,
        granted_by_user_id=acl.granted_by_user_id,
        created_at=acl.created_at,
    )


@router.post(
    "/{cred_id}/user-acl",
    response_model=UserACLOut,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать доступ конкретному пользователю (read/reveal, опц. write)",
)
@limiter.limit(get_settings().rate_limit_acl)
async def add_user_acl(
    cred_id: str,
    request: Request,
    payload: UserACLCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> UserACLOut:
    require_user_context(identity)
    acl = await user_acl_service.add(db, identity, cred_id, payload)
    return _to_out(acl)


@router.get(
    "/{cred_id}/user-acl",
    response_model=UserACLList,
    summary="Список user-ACL для credential",
)
async def list_user_acls(
    cred_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> UserACLList:
    require_user_context(identity)
    acls = await user_acl_service.list_for(db, identity, cred_id)
    return UserACLList(items=[_to_out(a) for a in acls])


@router.delete(
    "/{cred_id}/user-acl/{acl_id}",
    response_model=OkResponse,
    summary="Снять user-ACL",
)
async def revoke_user_acl(
    cred_id: str,
    acl_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    require_user_context(identity)
    await user_acl_service.revoke(db, identity, cred_id, acl_id)
    return OkResponse(ok=True)
