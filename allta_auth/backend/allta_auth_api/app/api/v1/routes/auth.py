from fastapi import APIRouter, Depends, HTTPException, status, Response, Security
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import jwt

from app.db.session import get_db
from app.api.v1.crud.user import get_user_by_login
from app.utils.security import verify_password
from app.api.v1.crud.token import create_active_token, revoke_token
from app.api.v1.crud.api_token import revoke_api_token_by_jti
from app.api.v1.schemas.token import TokenOut
from app.api.v1.dependencies import (
    get_current_user,
    get_token,
    extract_jti_from_token,
)
from app.utils.config import settings
from app.api.v1.schemas.user import UserRead

router = APIRouter(prefix="", tags=["Авторизация"])


@router.post(
    "/login",
    response_model=TokenOut,
    summary="Войти по логину и паролю и получить access token",
)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Аутентифицирует пользователя по логину и паролю.
    При успехе возвращает JWT и устанавливает `access_token` в cookie (HttpOnly).
    """
    user = get_user_by_login(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_entry = create_active_token(db, user.id)
    payload = {
        "jti": token_entry.jti,
        "user_id": user.id,
        "exp": int(token_entry.expires_at.timestamp()),
    }
    jwt_token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    return TokenOut(access_token=jwt_token, token_type="bearer")


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Выйти и отозвать текущий токен",
)
def logout(
    response: Response,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """
    Отзывает текущий access token и удаляет cookie `access_token`.
    """
    jti = extract_jti_from_token(token)
    revoke_token(db, jti)
    revoke_api_token_by_jti(db, jti)
    response.delete_cookie("access_token")


@router.get(
    "/verify",
    status_code=200,
    response_model=UserRead,
    summary="Проверить валидность токена и получить профиль",
)
def verify(
    current_user: UserRead = Security(get_current_user),
):
    """
    Возвращает данные текущего пользователя при валидном токене.
    Поддерживаются источники: Cookie, Authorization header.
    """
    return current_user
