from typing import Callable, Optional

import httpx
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    OAuth2PasswordBearer,
    HTTPBearer,
    HTTPAuthorizationCredentials,
)
from pydantic import BaseModel, Field

from app.utils.config import settings

# ------------------------------
# Модель ответа Auth-сервиса
# ------------------------------
class IntegrationCapabilities(BaseModel):
    docker_pull: bool = True
    docker_push: bool = False
    portainer_access: bool = False
    devpi_read: bool = True
    devpi_write: bool = False


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
        return "*" in self.permissions or permission in self.permissions


# -----------------------------------
# Swagger UI: логин через форму
#     (OAuth2 Password flow)
# -----------------------------------
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.AUTH_API_URL}/login",
    scheme_name="OAuth2Password",
    auto_error=False,
)

# -----------------------------------
# Swagger UI: ручная вставка JWT
#     (HTTP Bearer auth)
# -----------------------------------
bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    auto_error=False,
)


def get_token(
    request: Request,
    oauth2_token: Optional[str] = Security(oauth2_scheme),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> str:
    # 1) токен из OAuth2PasswordBearer (форма в Swagger)
    if oauth2_token:
        return oauth2_token

    # 2) токен из Authorization: Bearer <...>
    if bearer and bearer.scheme and bearer.scheme.lower() == "bearer":
        return bearer.credentials

    # 3) токен из secure-cookie
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    # нет ни одного источника — честно 401
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(get_token),
) -> AuthVerifyResponse:
    """
    Проверяем токен в Auth-сервисе через integrations/whoami.
    """
    url = f"{settings.AUTH_API_URL}/v1/integrations/whoami"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Auth service is unreachable",
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            raise HTTPException(
                status_code=e.response.status_code,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Auth service unavailable",
        )

    return AuthVerifyResponse(**resp.json())


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
    """
    Проверяем, что у пользователя role=admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user
