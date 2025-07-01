# file_api/dependencies.py

import os

import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyCookie

# URL вашего Auth-API (в нём должен быть endpoint GET /api/v1/auth/verify)
AUTH_VERIFY_URL = os.getenv(
    "AUTH_VERIFY_URL",
    "http://allta-auth-api:8000/api/auth/verify"  
)

# схемы для извлечения raw-token из Header или Cookie
bearer_scheme = HTTPBearer(auto_error=False)
cookie_scheme = APIKeyCookie(name="access_token", auto_error=False)

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)

async def get_raw_token(
    bearer: HTTPAuthorizationCredentials = Security(bearer_scheme),
    cookie: str = Security(cookie_scheme),
) -> str:
    """
    Пытаемся достать JWT из Authorization Bearer или из Cookie access_token.
    """
    if bearer and bearer.credentials:
        return bearer.credentials
    if cookie:
        return cookie
    raise credentials_exception

async def verify_with_auth_api(
    token: str = Depends(get_raw_token),
) -> dict:
    """
    Отправляем запрос на Auth-API, чтобы проверить токен.
    Если всё ок, возвращаем JSON payload ({"login":..., "is_admin":...}).
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(AUTH_VERIFY_URL, headers=headers, timeout=5.0)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Auth service unreachable: {e}"
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    return resp.json()
