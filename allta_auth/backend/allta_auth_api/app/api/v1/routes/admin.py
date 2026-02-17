from typing import List
from fastapi import APIRouter, Security, HTTPException, status, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.user import (
    UserCreate, UserRead, UserUpdate,
    PasswordResetAdmin,
)
from app.api.v1.crud.user import (
    create_user, get_users,
    update_user, delete_user,
)
from app.api.v1.crud.token import (
    revoke_all_tokens,
    revoke_all_tokens_global,
)
from app.api.v1.dependencies import (
    get_current_admin_user,
)

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)

# ——— админский блок ———

@router.post(
    "/",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать пользователя (admin)",
)
def admin_create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Создать нового пользователя.  
    Доступ: только администратор.
    """
    return create_user(db, body)


@router.get(
    "/",
    response_model=List[UserRead],
    summary="Список пользователей (admin)",
)
def admin_list_users(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Получить список всех пользователей.  
    Доступ: только администратор.
    """
    return get_users(db)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Обновить пользователя (admin)",
)
def admin_update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Обновить данные существующего пользователя.  
    Доступ: только администратор.
    """
    user = update_user(db, user_id, body)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить пользователя (admin)",
)
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Удалить пользователя по ID.  
    Доступ: только администратор.
    """
    delete_user(db, user_id)


@router.post(
    "/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сбросить пароль пользователя (admin)",
)
def admin_reset_user_password(
    user_id: int,
    body: PasswordResetAdmin,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Сбросить пароль пользователя по ID.  
    Доступ: только администратор.
    """
    user = update_user(db, user_id, UserUpdate(password=body.new_password))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")


@router.post(
    "/tokens/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать все обычные access-токены в системе (admin)",
)
def admin_revoke_all_tokens_global(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Отозвать **все обычные JWT access-токены** во всей системе.  
    Бессрочные API-токены этим endpoint не затрагиваются.
    Доступ: только администратор.
    """
    revoke_all_tokens_global(db)


@router.post(
    "/{user_id}/tokens/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать обычные access-токены пользователя (admin)",
)
def admin_revoke_user_tokens(
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    """
    Отозвать все обычные JWT access-токены конкретного пользователя по ID.  
    Бессрочные API-токены этим endpoint не затрагиваются.
    Доступ: только администратор.
    """
    revoke_all_tokens(db, user_id)
