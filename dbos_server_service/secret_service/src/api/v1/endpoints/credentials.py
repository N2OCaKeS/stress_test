"""HTTP-эндпоинты /credentials — CRUD + reveal + transfer + recover."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import BadRequestError
from src.dependencies.auth import CurrentIdentity, require_user_context
from src.dependencies.db import get_db
from src.models import Credential
from src.schemas.common import OkResponse
from src.schemas.credentials import (
    AdminDeleteRequest,
    CredentialCreate,
    CredentialList,
    CredentialRead,
    CredentialRevealResponse,
    CredentialUpdate,
    TransferRequest,
)
from src.services import credential_service

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _to_read(cred: Credential) -> CredentialRead:
    return CredentialRead(
        id=cred.id,
        name=cred.name,
        service=cred.service,
        scope=cred.scope,  # type: ignore[arg-type]
        owner_user_id=cred.owner_user_id,
        owner_dept_id=cred.owner_dept_id,
        login=cred.login,
        status=cred.status,  # type: ignore[arg-type]
        created_by=cred.created_by,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
        blocked_at=cred.blocked_at,
        blocked_reason=cred.blocked_reason,
    )


def _parse_cursor(raw: str | None) -> tuple[datetime, str] | None:
    if not raw:
        return None
    try:
        ts, cred_id = raw.split("|", 1)
        return datetime.fromisoformat(ts), cred_id
    except (ValueError, AttributeError) as exc:
        raise BadRequestError(
            error_code="INVALID_CURSOR",
            message="cursor format must be `<iso-timestamp>|<cred_id>`",
        ) from exc


@router.get(
    "",
    response_model=CredentialList,
    summary="Список видимых credentials с курсорной пагинацией",
)
async def list_credentials(
    identity: CurrentIdentity,
    scope: str | None = Query(default=None, description="Фильтр по scope."),
    service: str | None = Query(default=None, description="Фильтр по service."),
    status_filter: str | None = Query(
        default=None, alias="status", description="Фильтр по status."
    ),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    db: AsyncSession = Depends(get_db),
) -> CredentialList:
    """Список credentials, отфильтрованный по access-check."""
    require_user_context(identity)
    cursor_pair = _parse_cursor(cursor)
    items, next_cursor = await credential_service.list_visible(
        db,
        identity,
        scope=scope,
        service=service,
        status=status_filter,
        limit=limit,
        cursor=cursor_pair,
    )
    return CredentialList(
        items=[_to_read(c) for c in items],
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=CredentialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать credential (encrypted at-rest, plaintext не возвращается)",
)
async def create_credential(
    payload: CredentialCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    """POST /credentials."""
    require_user_context(identity)
    cred = await credential_service.create(db, identity, payload)
    return _to_read(cred)


@router.get(
    "/{cred_id}",
    response_model=CredentialRead,
    summary="Получить карточку credential (без secret)",
)
async def get_credential(
    cred_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_user_context(identity)
    cred = await credential_service.get(db, identity, cred_id)
    return _to_read(cred)


@router.patch(
    "/{cred_id}",
    response_model=CredentialRead,
    summary="Обновить name/login/secret credential",
)
async def update_credential(
    cred_id: str,
    payload: CredentialUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_user_context(identity)
    cred = await credential_service.update(db, identity, cred_id, payload)
    return _to_read(cred)


@router.delete(
    "/{cred_id}",
    response_model=OkResponse,
    summary="Удалить credential (admin override требует reason в body)",
)
async def delete_credential(
    cred_id: str,
    identity: CurrentIdentity,
    payload: AdminDeleteRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    require_user_context(identity)
    await credential_service.delete(db, identity, cred_id, payload)
    return OkResponse(ok=True)


@router.post(
    "/{cred_id}/reveal",
    response_model=CredentialRevealResponse,
    summary="Расшифровать secret (CRITICAL audit + 5-min throttle)",
)
async def reveal_credential(
    cred_id: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRevealResponse:
    require_user_context(identity)
    login, secret_b64 = await credential_service.reveal(db, identity, cred_id)
    return CredentialRevealResponse(login=login, secret_b64=secret_b64)


@router.post(
    "/{cred_id}/transfer",
    response_model=CredentialRead,
    summary="Передать ownership заблокированной credential (service_admin/account_admin)",
)
async def transfer_credential(
    cred_id: str,
    payload: TransferRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_user_context(identity)
    cred = await credential_service.transfer(db, identity, cred_id, payload)
    return _to_read(cred)


@router.post(
    "/{cred_id}/recover",
    response_model=CredentialRead,
    summary="Снять блокировку credential в окне 30 дней (service_admin/account_admin)",
)
async def recover_credential(
    cred_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_user_context(identity)
    cred = await credential_service.recover(db, identity, cred_id)
    return _to_read(cred)


# Forward export — для use в __init__-aggregator'ах если потребуется.
__all__ = ["router"]


# Helper для тестов: возможно нужен явный 204 без content (FastAPI делает).
_ = OkResponse  # unused import-guard
