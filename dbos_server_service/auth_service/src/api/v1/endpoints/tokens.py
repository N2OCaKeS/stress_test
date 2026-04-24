"""Personal access token endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.tokens import PATCreate, PATCreateResponse, PATListItem
from src.services import token_service

router = APIRouter(prefix="/tokens")


@router.post("", response_model=PATCreateResponse, status_code=201)
async def create_token(
    body: PATCreate,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> PATCreateResponse:
    return await token_service.create_pat(
        db=db,
        actor_id=identity.user_id,
        name=body.name,
        allowed_services=body.allowed_services,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("", response_model=list[PATListItem])
async def list_tokens(
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[PATListItem]:
    return await token_service.list_pats(
        db=db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{token_id}", response_model=OkResponse)
async def revoke_token(
    token_id: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await token_service.revoke_pat(
        db=db,
        actor_id=identity.user_id,
        token_id=token_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
