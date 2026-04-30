"""OAuth2 client management and token/authorize endpoints."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AnyAdmin, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreatedResponse,
    OAuthClientResponse,
    OAuthTokenRequest,
    OAuthTokenResponse,
)
from src.services import oauth_service

router = APIRouter(prefix="/oauth2")


# ── Client management ─────────────────────────────────────────────────────────

@router.post("/clients", response_model=OAuthClientCreatedResponse, status_code=201)
async def create_client(
    body: OAuthClientCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OAuthClientCreatedResponse:
    return await oauth_service.create_client(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/clients", response_model=list[OAuthClientResponse])
async def list_clients(
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
    department_id: str | None = Query(default=None),
) -> list[OAuthClientResponse]:
    return await oauth_service.list_clients(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/clients/{client_id}", response_model=OkResponse)
async def delete_client(
    client_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await oauth_service.delete_client(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        client_db_id=client_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


# ── Authorization code flow ───────────────────────────────────────────────────

@router.get("/authorize")
async def authorize(
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(default=""),
    state: str | None = Query(default=None),
    response_type: str = Query(default="code"),
):
    """Issue an authorization code and redirect to redirect_uri."""
    scopes = scope.split() if scope else []
    code = await oauth_service.issue_authorization_code(
        db=db,
        client_id=client_id,
        user_id=identity.user_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        request_id=getattr(request.state, "request_id", None),
    )
    location = f"{redirect_uri}?code={code}"
    if state:
        location += f"&state={quote(state, safe='')}"
    return RedirectResponse(url=location, status_code=302)


@router.post("/token", response_model=OAuthTokenResponse)
async def token(
    body: OAuthTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OAuthTokenResponse:
    """Exchange authorization code or issue client_credentials token."""
    request_id = getattr(request.state, "request_id", None)
    if body.grant_type == "authorization_code":
        return await oauth_service.exchange_code(
            db=db,
            client_id=body.client_id or "",
            client_secret=body.client_secret or "",
            code=body.code or "",
            redirect_uri=body.redirect_uri or "",
            request_id=request_id,
        )
    if body.grant_type == "client_credentials":
        return await oauth_service.client_credentials_token(
            db=db,
            client_id=body.client_id or "",
            client_secret=body.client_secret or "",
            request_id=request_id,
        )
    from src.core.exceptions import DomainValidationError
    raise DomainValidationError(
        error_code="UNSUPPORTED_GRANT_TYPE",
        message=f"grant_type '{body.grant_type}' is not supported",
    )
