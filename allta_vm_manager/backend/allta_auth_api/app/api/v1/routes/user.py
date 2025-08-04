from typing import List

from fastapi import APIRouter, Security, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.user import (
    UserRead, UserUpdate,
    PasswordReset
)
from app.api.v1.crud.user import update_user

from app.api.v1.dependencies import (
    get_current_user,
    oauth2_scheme
)

router = APIRouter(
    prefix="/user",
    tags=["User"],
)

@router.get(
    "/me",
    response_model=UserRead,
    summary="Получить свой профиль"
)
def read_own_profile(
    current_user = Security(get_current_user, scopes=[]),
):
    return current_user


@router.post(
    "/me/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сбросить свой пароль"
)
def reset_own_password(
    body: PasswordReset,
    db: Session         = Depends(get_db),
    current_user        = Security(get_current_user, scopes=[])
):
    updated = update_user(
        db, current_user.id,
        UserUpdate(password=body.new_password)
    )
    if not updated:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not reset password")