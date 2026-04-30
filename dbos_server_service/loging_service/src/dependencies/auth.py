"""Authentication dependencies for loging_service.

Three security levels:
  require_service_token — shared SERVICE_API_KEY for service-to-service calls (POST events only)
  require_admin         — loging_admin JWT: full access including rules management
  require_reader        — read-only access: loging_admin | loging_reader | department_admin
                          department_admin and loging_reader are auto-scoped to their department
"""

import secrets
from typing import Annotated

import httpx
from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import get_settings
from src.core.exceptions import AppException

_bearer = HTTPBearer(auto_error=False)

# Roles that can read logs (but not manage rules)
_READER_ROLES = {"loging_admin", "loging_reader", "department_admin", "account_admin"}

# Roles scoped to their own department only
_DEPT_SCOPED_ROLES = {"loging_reader", "department_admin"}


def require_service_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    settings = get_settings()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, settings.service_api_key
    ):
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_TOKEN",
            message="Valid service API key required",
        )


def _fetch_identity(credentials: HTTPAuthorizationCredentials | None, request: Request) -> dict:
    """Shared helper: fetch identity from auth_service, store in request.state."""
    if credentials is None:
        raise AppException(http_status=401, error_code="MISSING_TOKEN",
                           message="Authentication required")

    settings = get_settings()
    if not settings.auth_service_url:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_NOT_CONFIGURED",
                           message="AUTH_SERVICE_URL is not configured")
    try:
        resp = httpx.get(
            f"{settings.auth_service_url}/api/auth/v1/me",
            headers={"Authorization": f"Bearer {credentials.credentials}"},
            timeout=3.0,
        )
    except httpx.TimeoutException:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_TIMEOUT",
                           message="Auth service did not respond in time")
    except httpx.ConnectError:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_UNREACHABLE",
                           message="Unable to connect to auth service")
    except Exception as exc:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message=f"Unexpected error: {type(exc).__name__}")

    if resp.status_code == 401:
        raise AppException(http_status=401, error_code="INVALID_TOKEN",
                           message="Invalid or expired token")
    if resp.status_code != 200:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message=f"Auth service returned {resp.status_code}")

    identity = resp.json()
    # Save before role check so audit middleware always has actor info
    request.state.auth_identity = identity

    # Also check loging_service service roles (user might have reader role via service role system)
    loging_svc_roles = identity.get("service_roles", {}).get("loging_service", [])
    identity["_loging_service_roles"] = loging_svc_roles

    return identity


def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Require platform_role=loging_admin. Full access including rules management."""
    identity = _fetch_identity(credentials, request)
    if identity.get("platform_role") != "loging_admin":
        raise AppException(
            http_status=403,
            error_code="INSUFFICIENT_ROLE",
            message="platform_role=loging_admin is required to manage loging_service",
        )
    identity["_dept_scope"] = None  # loging_admin sees everything
    return identity


def require_reader(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Read-only access. Accepted roles:
      loging_admin    → no department scope (sees all)
      account_admin   → no department scope (sees all)
      loging_reader   → scoped to own department_id
      department_admin→ scoped to own department_id
      any user with reader/operator/admin role in loging_service service → scoped to own dept
    """
    identity = _fetch_identity(credentials, request)
    role = identity.get("platform_role")
    loging_roles = identity.get("_loging_service_roles", [])

    # Determine access and scope
    if role in ("loging_admin", "account_admin"):
        identity["_dept_scope"] = None
        return identity

    if role in _DEPT_SCOPED_ROLES or any(r in loging_roles for r in ("reader", "operator", "admin")):
        # Scoped to own department
        dept_id = identity.get("department_id")
        if not dept_id:
            raise AppException(
                http_status=403, error_code="NO_DEPARTMENT",
                message="User has no department assigned — cannot scope log access",
            )
        identity["_dept_scope"] = dept_id
        return identity

    raise AppException(
        http_status=403,
        error_code="INSUFFICIENT_ROLE",
        message=(
            "Access requires: platform_role in (loging_admin, loging_reader, "
            "account_admin, department_admin) or reader/operator/admin role in loging_service"
        ),
    )


AdminIdentity = Annotated[dict, Depends(require_admin)]
ReaderIdentity = Annotated[dict, Depends(require_reader)]
