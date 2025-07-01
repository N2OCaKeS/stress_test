# app/api/v1/dependencies.py

from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    OAuth2PasswordBearer,
    HTTPBearer,
    HTTPAuthorizationCredentials,
    APIKeyCookie
)
from pydantic import BaseModel

from app.utils.config import settings

# ------------------------------
# Модель ответа Auth-сервиса
# ------------------------------
class AuthVerifyResponse(BaseModel):
    login: str
    is_admin: bool
    id: int


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
    """
    Порядок извлечения токена:
      1) OAuth2PasswordBearer (логин через форму)
      2) HTTPBearer        (вручную ввести JWT)
      3) secure-cookie 'access_token'
    """
    if oauth2_token:
        return oauth2_token
    if bearer and bearer.scheme.lower() == "bearer":
        return bearer.credentials
    if not oauth2_scheme and not bearer and not bearer.scheme.lower() == "bearer":
        token = request.cookies.get("access_token")
        return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(get_token),
) -> AuthVerifyResponse:
    """
    Проверяем токен в Auth-сервисе (/verify).
    """
    url = f"{settings.AUTH_API_URL}/verify"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
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


def get_current_admin_user(
    user: AuthVerifyResponse = Depends(get_current_user)
) -> AuthVerifyResponse:
    """
    Проверяем, что у пользователя is_admin = True.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user
