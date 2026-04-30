"""Login proxy for Swagger /docs.

POST /token — OAuth2 password flow.
Proxies credentials to auth_service and returns the JWT.
Only users with platform_role=loging_admin can use admin endpoints.
"""

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends

from src.core.config import get_settings

router = APIRouter()


@router.post("/token", include_in_schema=False)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if not settings.auth_service_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AUTH_SERVICE_URL is not configured",
        )
    try:
        resp = httpx.post(
            f"{settings.auth_service_url}/api/auth/v1/token",
            data={"username": form_data.username, "password": form_data.password},
            timeout=5.0,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to reach auth service: {exc}",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    data = resp.json()
    return {"access_token": data["access_token"], "token_type": "bearer"}
