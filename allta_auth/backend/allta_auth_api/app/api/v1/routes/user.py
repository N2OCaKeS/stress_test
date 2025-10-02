from typing import List

from fastapi import APIRouter, Security, HTTPException, status, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.user import UserRead, UserUpdate, PasswordReset
from app.api.v1.crud.user import update_user
from app.api.v1.dependencies import get_current_user

router = APIRouter(
    prefix="/user",
    tags=["User"],
)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Получить профиль текущего пользователя",
)
def read_own_profile(
    current_user=Security(get_current_user, scopes=[]),
):
    """
    Возвращает данные аутентифицированного пользователя.
    """
    return current_user


@router.post(
    "/me/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сменить пароль текущего пользователя",
)
def reset_own_password(
    body: PasswordReset,
    db: Session = Depends(get_db),
    current_user=Security(get_current_user, scopes=[]),
):
    """
    Изменяет пароль текущего пользователя.
    Требует валидной аутентификации.
    """
    updated = update_user(db, current_user.id, UserUpdate(password=body.new_password))
    if not updated:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not reset password")
