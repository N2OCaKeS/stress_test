from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Response, Security
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import jwt

from app.db.session import get_db
from app.api.v1.crud.user import get_user_by_login
from app.utils.security import verify_password
from app.api.v1.crud.token import create_active_token, revoke_token
from app.api.v1.schemas.token import TokenOut
from app.api.v1.dependencies import get_current_user, oauth2_scheme
from app.utils.config import settings
from app.api.v1.schemas.token import TokenData
from app.api.v1.schemas.user import UserRead

router = APIRouter(prefix="", tags=["Auth"])

@router.post("/login", response_model=TokenOut)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
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
        "exp": int(token_entry.expires_at.timestamp())
    }
    jwt_token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False,
    )
    return TokenOut(access_token=jwt_token, token_type="bearer")

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    revoke_token(db, token)
    response.delete_cookie("access_token")

@router.get(
    "/verify",
    status_code=200,
    response_model=UserRead,  # Указываем модель ответа
    summary="Проверить валидность токена"
)
def verify(
    current_user: UserRead = Security(get_current_user),
):
    """
    Возвращает данные текущего пользователя,
    если токен валиден (из cookie,Authorization или header).
    """
    return current_user
