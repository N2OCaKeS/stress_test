"""Docker registry token auth + per-department configuration endpoints.

Docker registry config.yml:
  auth:
    token:
      realm: https://<host>/api/auth/v1/docker/token
      service: registry.example.com
      issuer: auth_service
"""

import base64

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.docker_jwt import get_jwks, get_public_key_pem
from src.core.exceptions import AuthenticationError
from src.dependencies.auth import AccountAdmin, AnyAdmin
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.docker_registry import (
    DockerRegistryConfigCreate,
    DockerRegistryConfigResponse,
    DockerRegistryConfigUpdate,
    DockerTokenResponse,
)
from src.services import docker_registry_service

router = APIRouter(prefix="/docker")


def _parse_basic_auth(authorization: str | None) -> tuple[str, str]:
    if not authorization or not authorization.lower().startswith("basic "):
        raise AuthenticationError(
            error_code="MISSING_CREDENTIALS",
            message="Basic authentication required",
        )
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
        return username, password
    except Exception:
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Malformed Basic auth header")


# ── Config management (dept_admin or account_admin) ───────────────────────────

@router.put(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Enable / replace Docker registry config for a department",
)
async def create_or_replace_registry_config(
    department_id: str,
    body: DockerRegistryConfigCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    return await docker_registry_service.create_or_replace_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Update Docker registry config for a department",
)
async def update_registry_config(
    department_id: str,
    body: DockerRegistryConfigUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    return await docker_registry_service.update_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Get Docker registry config for a department",
)
async def get_registry_config(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    return await docker_registry_service.get_config(
        db=db,
        department_id=department_id,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/registry/{department_id}",
    response_model=OkResponse,
    summary="Disable Docker registry for a department",
)
async def disable_registry(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await docker_registry_service.delete_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


# ── Token endpoint (Docker protocol) ─────────────────────────────────────────

@router.get(
    "/token",
    response_model=DockerTokenResponse,
    summary="Docker registry token auth (Basic credentials → scoped JWT)",
)
async def docker_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: str = Query(default=""),
    scope: str = Query(default=""),
    account: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> DockerTokenResponse:
    """
    Called by Docker clients and the registry to obtain a scoped bearer token.

    Auth options (via Basic auth header):
    - `username:password` — standard credentials
    - `username:dbos_pat_...` — Personal Access Token as password
    - `botname:dbos_bot_...` — bot token as password
    """
    username, password = _parse_basic_auth(authorization)
    return await docker_registry_service.issue_token(
        db=db,
        username=username,
        password=password,
        service=service,
        scope=scope,
        request_id=getattr(request.state, "request_id", None),
    )


# ── Public key endpoints (for registry rootcertbundle configuration) ──────────

@router.get(
    "/certs",
    response_class=PlainTextResponse,
    summary="RSA public key in PEM format (use as Docker registry rootcertbundle)",
    tags=["docker-registry"],
)
async def docker_public_key() -> str:
    """
    Returns the RSA public key used to sign Docker JWT tokens.

    Save to a file and reference it in the Docker registry config:
      auth.token.rootcertbundle: /path/to/this.pem
    """
    return get_public_key_pem()


@router.get(
    "/jwks",
    summary="JWKS endpoint — public keys for JWT verification",
    tags=["docker-registry"],
)
async def docker_jwks() -> dict:
    """JSON Web Key Set — can be used by JWT-aware tools to verify Docker tokens."""
    return get_jwks()
