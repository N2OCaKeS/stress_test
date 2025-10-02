# app/crud/user.py

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.api.v1.models.user import User
from app.api.v1.schemas.user import UserCreate, UserUpdate
from app.utils.security import get_password_hash

from app.api.v1.crud.token import revoke_all_tokens_for_user
from app.api.v1.crud.api_token import revoke_all_api_tokens_for_user


def get_user(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_login(db: Session, login: str) -> Optional[User]:
    return db.query(User).filter(User.login == login).first()


def get_user_count(db: Session) -> int:
    return db.query(func.count(User.id)).scalar()


def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[User]:
    return db.query(User).offset(skip).limit(limit).all()


def create_user(db: Session, user_in: UserCreate) -> User:
    """
    Создаёт пользователя. Если логин уже занят — возвращает HTTP 400.
    """
    hashed = get_password_hash(user_in.password)
    db_user = User(
        login=user_in.login,
        password_hash=hashed,
        is_admin=user_in.is_admin,
    )
    db.add(db_user)
    try:
        db.commit()
        db.refresh(db_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{user_in.login}' already exists"
        )
    return db_user



def update_user(db: Session, user_id: int, user_in: UserUpdate) -> Optional[User]:
    user = get_user(db, user_id)
    if not user:
        return None

    # меняем поля
    if user_in.password:
        user.password_hash = get_password_hash(user_in.password)
    if user_in.is_admin is not None:
        user.is_admin = user_in.is_admin

    db.add(user)
    db.commit()
    db.refresh(user)

    # после любых изменений — чистим все токены этого пользователя
    revoke_all_tokens_for_user(db, user.id)
    revoke_all_api_tokens_for_user(db, user.id)

    return user


def delete_user(db: Session, user_id: int) -> None:
    user = get_user(db, user_id)
    if not user:
        return

    # сначала чистим все токены
    revoke_all_tokens_for_user(db, user.id)
    revoke_all_api_tokens_for_user(db, user.id)

    # затем удаляем пользователя
    db.delete(user)
    db.commit()
