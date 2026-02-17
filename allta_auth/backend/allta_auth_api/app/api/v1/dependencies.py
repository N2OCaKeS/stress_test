# app/api/v1/dependencies.py

from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import (
    OAuth2PasswordBearer,
    HTTPBearer,
    HTTPAuthorizationCredentials,
)
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from pydantic import ValidationError

from app.db.session import get_db
from app.api.v1.crud.token import get_active_token
from app.api.v1.crud.api_token import get_api_token_by_jti
from app.api.v1.crud.user import get_user
from app.api.v1.schemas.token import TokenData
from app.api.v1.models.user import User
from app.utils.config import settings

# Swagger UI: форма логина (OAuth2 Password Flow)
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/login",
    scheme_name="OAuth2Password",
    auto_error=False,
)
# Swagger UI: ручной ввод Bearer-токена
bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    auto_error=False,
)


def decode_jwt_payload(token: str, verify_exp: bool = True) -> dict:
    options = None if verify_exp else {"verify_exp": False}
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        options=options,
    )


def extract_jti_from_token(token: str) -> str:
    try:
        payload = decode_jwt_payload(token, verify_exp=False)
    except JWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain jti",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return jti


def get_token(
    request: Request,
    oauth2_token: Optional[str] = Depends(oauth2_scheme),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """
    Извлекаем JWT в порядке:
    1) OAuth2PasswordBearer (логин через Swagger)
    2) HTTPBearer (вручную вставленный токен)
    3) secure cookie 'access_token'
    """
    if oauth2_token:
        return oauth2_token
    if bearer and bearer.scheme.lower() == "bearer":
        return bearer.credentials
    cookie = request.cookies.get("access_token")
    if cookie:
        return cookie

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve_user_from_token(token: str, db: Session) -> User:
    """
    1) Пробуем декодировать стандартный JWT (с exp и проверкой срока).
    2) Если Pydantic жалуется на отсутствие exp — считаем это бессрочным API-токеном:
       декодируем без проверки exp и проверяем его JTI в таблице api_tokens.
    3) В обоих случаях возвращаем User или кидаем 401/403.
    """
    # --- ПЕРВЫЙ СЛУЧАЙ: обычный токен с exp ---
    try:
        payload = decode_jwt_payload(token, verify_exp=True)
        data = TokenData(**payload)  # тут Pydantic проверит наличие jti, user_id, exp
    except ValidationError:
        # payload не соответствует TokenData (например, нет exp) — считаем API-токеном
        try:
            # декодируем без проверки срока
            payload = decode_jwt_payload(token, verify_exp=False)
        except JWTError:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        jti = payload.get("jti")
        user_id = payload.get("user_id")
        if not jti or not user_id:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # проверяем, что этот API-токен не отозван
        api_token = get_api_token_by_jti(db, jti)
        if not api_token or api_token.revoked:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="API token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = get_user(db, user_id)
        if not user:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    except JWTError:
        # сломался на валидации / просрочен
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- ВТОРОЙ СЛУЧАЙ: валидный стандартный токен ---
    # Проверяем, что он в таблице активных
    if not get_active_token(db, data.jti):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user(db, data.user_id)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
) -> User:
    return resolve_user_from_token(token, db)


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Проверяем, что у пользователя admin-роль.
    """
    if not current_user.is_admin_effective():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
