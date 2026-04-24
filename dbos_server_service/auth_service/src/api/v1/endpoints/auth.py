"""Login, refresh, logout, and identity endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.auth import IdentityContext, LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, RefreshResponse
from src.schemas.common import OkResponse
from src.services import auth_service

router = APIRouter()


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "auth_service"}


@router.get("/ready")
async def readiness() -> dict[str, str]:
    return {"status": "ready", "service": "auth_service"}


@router.post(
    "/token",
    response_model=LoginResponse,
    summary="OAuth2 token (Swagger UI login form)",
    include_in_schema=False,
)
async def token_form(
    form: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """OAuth2 Password flow endpoint used by Swagger UI's Authorize dialog."""
    return await auth_service.login(
        db=db,
        username=form.username,
        password=form.password,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("User-Agent") if request else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await auth_service.login(
        db=db,
        username=body.username,
        password=body.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    return await auth_service.refresh(
        db=db,
        raw_refresh_token=body.refresh_token,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/logout", response_model=OkResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await auth_service.logout(
        db=db,
        raw_refresh_token=body.refresh_token,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.get("/me", response_model=IdentityContext)
async def me(request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db)) -> IdentityContext:
    return await auth_service.get_identity(
        db=db,
        user_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
