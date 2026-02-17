from typing import Callable, Optional

import httpx
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2PasswordBearer,
)
from pydantic import BaseModel, Field

from app.utils.config import settings


class IntegrationCapabilities(BaseModel):
    docker_pull: bool = True
    docker_push: bool = False
    portainer_access: bool = False
    devpi_read: bool = True
    devpi_write: bool = False
    server_manage: bool = False
    vm_manage: bool = False


class AuthVerifyResponse(BaseModel):
    login: str
    id: int
    role: str = "user"
    permissions: list[str] = Field(default_factory=list)
    capabilities: IntegrationCapabilities = Field(default_factory=IntegrationCapabilities)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def has_permission(self, permission: str) -> bool:
        return self.is_admin or "*" in self.permissions or permission in self.permissions


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.AUTH_API_URL}/login",
    scheme_name="OAuth2Password",
    auto_error=False,
)

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    auto_error=False,
)


def get_token(
    request: Request,
    oauth2_token: Optional[str] = Security(oauth2_scheme),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> str:
    if oauth2_token:
        return oauth2_token
    if bearer and bearer.scheme and bearer.scheme.lower() == "bearer":
        return bearer.credentials
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _normalize_auth_payload(raw_payload: dict) -> dict:
    payload = dict(raw_payload)
    if "role" not in payload:
        payload["role"] = "admin" if payload.get("is_admin") else "user"
    payload.setdefault("permissions", ["*"] if payload["role"] == "admin" else [])
    payload.setdefault("capabilities", {})
    return payload


def _request_auth_profile(url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(url, headers=headers, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def get_current_user(
    token: str = Depends(get_token),
) -> AuthVerifyResponse:
    whoami_url = f"{settings.AUTH_API_URL}/v1/integrations/whoami"
    legacy_verify_url = f"{settings.AUTH_API_URL}/verify"
    try:
        payload = _request_auth_profile(whoami_url, token)
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Auth service is unreachable",
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == status.HTTP_404_NOT_FOUND:
            try:
                payload = _request_auth_profile(legacy_verify_url, token)
            except httpx.RequestError:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Auth service is unreachable",
                )
            except httpx.HTTPStatusError as legacy_error:
                if legacy_error.response.status_code in (
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                ):
                    raise HTTPException(
                        status_code=legacy_error.response.status_code,
                        detail="Invalid or expired token",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Auth service unavailable",
                )
        elif e.response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ):
            raise HTTPException(
                status_code=e.response.status_code,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Auth service unavailable",
            )

    return AuthVerifyResponse(**_normalize_auth_payload(payload))


def require_permission(permission: str) -> Callable:
    def _checker(user: AuthVerifyResponse = Depends(get_current_user)) -> AuthVerifyResponse:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: '{permission}' required",
            )
        return user

    return _checker


def get_current_admin_user(
    user: AuthVerifyResponse = Depends(get_current_user)
) -> AuthVerifyResponse:
    required_permission = settings.SERVER_MANAGE_PERMISSION
    if not user.has_permission(required_permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions: '{required_permission}' required",
        )
    return user
