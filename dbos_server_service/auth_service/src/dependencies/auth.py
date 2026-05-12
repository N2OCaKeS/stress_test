"""Authentication and authorization dependencies."""

from typing import Annotated

from fastapi import Depends, Request

from src.core.constants import PlatformRole
from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import decode_access_token
from src.schemas.auth import IdentityContext


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _payload_to_identity(payload: dict) -> IdentityContext:
    return IdentityContext(
        user_id=payload["sub"],
        username=payload.get("username", ""),
        department_id=payload.get("department_id", ""),
        allowed_services=payload.get("allowed_services", []),
        service_roles=payload.get("service_roles", {}),
        platform_role=payload.get("platform_role"),
    )


async def get_current_identity(
    request: Request,
) -> IdentityContext:
    """Resolve the current authenticated identity from Bearer JWT."""
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_EXPIRED",
            message="Missing bearer token",
        )
    try:
        payload = decode_access_token(token)
        return _payload_to_identity(payload)
    except Exception:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_EXPIRED",
            message="Invalid or expired token",
        )


CurrentIdentity = Annotated[IdentityContext, Depends(get_current_identity)]


def require_account_admin(identity: CurrentIdentity) -> IdentityContext:
    if identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        raise AuthorizationError(
            error_code="ROLE_REQUIRED",
            message="account_admin role required",
        )
    return identity


def require_any_admin(identity: CurrentIdentity) -> IdentityContext:
    if identity.platform_role not in (PlatformRole.ACCOUNT_ADMIN, PlatformRole.DEPARTMENT_ADMIN):
        raise AuthorizationError(
            error_code="ROLE_REQUIRED",
            message="Admin role required",
        )
    return identity


AccountAdmin = Annotated[IdentityContext, Depends(require_account_admin)]
AnyAdmin = Annotated[IdentityContext, Depends(require_any_admin)]
