"""Эндпоинты Personal Access Tokens (PAT)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.tokens import PATCreate, PATCreateResponse, PATListItem
from src.services import token_service

router = APIRouter(prefix="/tokens")


@router.post(
    "",
    response_model=PATCreateResponse,
    status_code=201,
    summary="Создать PAT",
    description="Возвращает raw-токен ОДИН РАЗ (в БД только hash). Префикс `dbos_pat_…`.",
)
async def create_token(
    body: PATCreate,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> PATCreateResponse:
    """Создать Personal Access Token.

    Что делает:
        Генерит opaque-секрет с префиксом `dbos_pat_…`, в БД лежит только
        SHA-256 hash + префикс. Raw-значение возвращается ровно один раз.
        `allowed_services` ограничивает scope токена.

    Доступ:
        Любой залогиненный юзер — создаёт PAT только себе.
    """
    return await token_service.create_pat(
        db=db,
        actor_id=identity.user_id,
        name=body.name,
        allowed_services=body.allowed_services,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "",
    response_model=list[PATListItem],
    summary="Список своих PAT",
    description="Только метаданные (prefix, created/expires/last_used). Raw-значения не отдаются.",
)
async def list_tokens(
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[PATListItem]:
    """Список своих PAT — только метаданные."""
    return await token_service.list_pats(
        db=db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{token_id}",
    response_model=OkResponse,
    summary="Отозвать свой PAT",
)
async def revoke_token(
    token_id: str,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Помечает PAT как revoked. Юзер может отзывать только свои токены."""
    await token_service.revoke_pat(
        db=db,
        actor_id=identity.user_id,
        token_id=token_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
