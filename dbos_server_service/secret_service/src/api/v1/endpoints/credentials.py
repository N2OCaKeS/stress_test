"""HTTP-эндпоинты /credentials — CRUD + reveal + transfer + recover."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import BadRequestError
from src.core.limiter import limiter
from src.dependencies.auth import (
    CurrentIdentity,
    require_transfer_recover_context,
    require_user_context,
)
from src.dependencies.db import get_db
from src.models import Credential
from src.schemas.common import OkResponse
from src.schemas.credentials import (
    AdminDeleteRequest,
    CredentialCreate,
    CredentialGuestList,
    CredentialGuestRead,
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
        visible_to_dept=cred.visible_to_dept,
        valid_from=cred.valid_from,
        valid_to=cred.valid_to,
    )


def _to_guest_read(cred: Credential) -> CredentialGuestRead:
    """Минимальная проекция для guest-роли — без owner/login/timestamps/created_by."""
    return CredentialGuestRead(
        id=cred.id,
        name=cred.name,
        service=cred.service,
        scope=cred.scope,  # type: ignore[arg-type]
        visible_to_dept=cred.visible_to_dept,
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
):
    """Список credentials, отфильтрованный по access-check.

    Для роли `guest` (одна-единственная роль `guest` в secret_service) возвращается
    урезанный shape `CredentialGuestList`: только cred'ы своего dep'а с
    `visible_to_dept=True`, и в каждой строке — лишь (id, name, service, scope,
    visible_to_dept). Никакого owner_user_id, login, created_by, timestamps,
    blocked-полей. Сделано так, чтобы guest «знал о существовании» секрета и
    мог запросить доступ через dep_admin'а, но не получал metadata-leak'а.
    """
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
    if credential_service.is_guest_only(identity):
        guest_resp = CredentialGuestList(
            items=[_to_guest_read(c) for c in items],
            next_cursor=next_cursor,
        )
        return JSONResponse(content=guest_resp.model_dump(mode="json"))
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
@limiter.limit(get_settings().rate_limit_create)
async def create_credential(
    request: Request,
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
@limiter.limit(get_settings().rate_limit_delete)
async def delete_credential(
    cred_id: str,
    request: Request,
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
@limiter.limit(get_settings().rate_limit_reveal)
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
    summary="Передать ownership заблокированной credential (admin secret_service своего dept'а / account_admin)",
)
@limiter.limit(get_settings().rate_limit_transfer)
async def transfer_credential(
    cred_id: str,
    request: Request,
    payload: TransferRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_transfer_recover_context(identity)
    cred = await credential_service.transfer(db, identity, cred_id, payload)
    return _to_read(cred)


@router.post(
    "/{cred_id}/recover",
    response_model=CredentialRead,
    summary="Снять блокировку credential в окне 30 дней (admin secret_service своего dept'а / account_admin)",
)
@limiter.limit(get_settings().rate_limit_recover)
async def recover_credential(
    cred_id: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> CredentialRead:
    require_transfer_recover_context(identity)
    cred = await credential_service.recover(db, identity, cred_id)
    return _to_read(cred)


__all__ = ["router"]
