"""Bot and service-account endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AnyAdmin, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.bots import BotCreate, BotResponse, BotTokenCreate, BotTokenCreateResponse, BotTokenListItem, BotUpdate
from src.schemas.common import OkResponse
from src.services import bot_service

router = APIRouter(prefix="/bots")


@router.post("", response_model=BotResponse, status_code=201)
async def create_bot(
    body: BotCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    return await bot_service.create_bot(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("", response_model=list[BotResponse])
async def list_bots(
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[BotResponse]:
    return await bot_service.list_bots(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{bot_id}", response_model=BotResponse)
async def update_bot(
    bot_id: str,
    body: BotUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    return await bot_service.update_bot(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{bot_id}/tokens", response_model=BotTokenCreateResponse, status_code=201)
async def create_bot_token(
    bot_id: str,
    body: BotTokenCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotTokenCreateResponse:
    return await bot_service.create_bot_token(
        db=db,
        actor_id=identity.user_id,
        bot_id=bot_id,
        name=body.name,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/{bot_id}/tokens", response_model=list[BotTokenListItem])
async def list_bot_tokens(
    bot_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[BotTokenListItem]:
    return await bot_service.list_bot_tokens(
        db=db,
        bot_id=bot_id,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{bot_id}/tokens/{token_id}", response_model=OkResponse)
async def revoke_bot_token(
    bot_id: str,
    token_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await bot_service.revoke_bot_token(
        db=db,
        actor_id=identity.user_id,
        bot_id=bot_id,
        token_id=token_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
