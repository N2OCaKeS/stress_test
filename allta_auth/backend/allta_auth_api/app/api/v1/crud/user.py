# app/crud/user.py

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.api.v1.models.access_control import GROUP_GUEST, ROLE_USER, Group, Role
from app.api.v1.models.user import User
from app.api.v1.schemas.user import UserCreate, UserUpdate
from app.utils.security import get_password_hash

from app.api.v1.crud.token import revoke_all_tokens_for_user
from app.api.v1.crud.api_token import revoke_all_api_tokens_for_user


def _get_role_or_400(db: Session, role_name: str) -> Role:
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{role_name}' not found",
        )
    return role


def _resolve_role_name_for_create(user_in: UserCreate) -> str:
    return user_in.role or ROLE_USER


def _ensure_default_guest_group(db: Session, user: User) -> None:
    guest_group = db.query(Group).filter(Group.name == GROUP_GUEST).first()
    if guest_group and guest_group not in user.groups:
        user.groups.append(guest_group)


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
    role_name = _resolve_role_name_for_create(user_in)
    role = _get_role_or_400(db, role_name)
    hashed = get_password_hash(user_in.password)
    db_user = User(
        login=user_in.login,
        password_hash=hashed,
        role_id=role.id,
    )
    _ensure_default_guest_group(db, db_user)
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

    if user_in.role is not None:
        role = _get_role_or_400(db, user_in.role)
        user.role_id = role.id
        user.role = role

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
