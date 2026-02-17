from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.models.access_control import Group, Permission, Role
from app.api.v1.models.user import User


def list_permissions(db: Session) -> List[Permission]:
    return db.query(Permission).order_by(Permission.code.asc()).all()


def get_permission(db: Session, permission_id: int) -> Optional[Permission]:
    return db.query(Permission).filter(Permission.id == permission_id).first()


def get_permission_by_code(db: Session, code: str) -> Optional[Permission]:
    return db.query(Permission).filter(Permission.code == code).first()


def create_permission(db: Session, code: str, description: Optional[str]) -> Permission:
    perm = Permission(code=code, description=description)
    db.add(perm)
    try:
        db.commit()
        db.refresh(perm)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Permission '{code}' already exists",
        )
    return perm


def list_roles(db: Session) -> List[Role]:
    return db.query(Role).order_by(Role.name.asc()).all()


def get_role(db: Session, role_id: int) -> Optional[Role]:
    return db.query(Role).filter(Role.id == role_id).first()


def get_role_by_name(db: Session, name: str) -> Optional[Role]:
    return db.query(Role).filter(Role.name == name).first()


def create_role(db: Session, name: str, description: Optional[str]) -> Role:
    role = Role(name=name, description=description)
    db.add(role)
    try:
        db.commit()
        db.refresh(role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{name}' already exists",
        )
    return role


def update_role(
    db: Session,
    role_id: int,
    *,
    name: Optional[str],
    description: Optional[str],
) -> Optional[Role]:
    role = get_role(db, role_id)
    if not role:
        return None

    if name is not None:
        role.name = name
    if description is not None:
        role.description = description

    try:
        db.add(role)
        db.commit()
        db.refresh(role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{name}' already exists",
        )
    return role


def add_permission_to_role(db: Session, role: Role, permission: Permission) -> None:
    if permission not in role.permissions:
        role.permissions.append(permission)
        db.add(role)
        db.commit()


def remove_permission_from_role(db: Session, role: Role, permission: Permission) -> None:
    if permission in role.permissions:
        role.permissions.remove(permission)
        db.add(role)
        db.commit()


def list_groups(db: Session) -> List[Group]:
    return db.query(Group).order_by(Group.name.asc()).all()


def get_group(db: Session, group_id: int) -> Optional[Group]:
    return db.query(Group).filter(Group.id == group_id).first()


def create_group(db: Session, name: str, description: Optional[str]) -> Group:
    group = Group(name=name, description=description)
    db.add(group)
    try:
        db.commit()
        db.refresh(group)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Group '{name}' already exists",
        )
    return group


def update_group(
    db: Session,
    group_id: int,
    *,
    name: Optional[str],
    description: Optional[str],
) -> Optional[Group]:
    group = get_group(db, group_id)
    if not group:
        return None

    if name is not None:
        group.name = name
    if description is not None:
        group.description = description

    try:
        db.add(group)
        db.commit()
        db.refresh(group)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Group '{name}' already exists",
        )
    return group


def add_permission_to_group(db: Session, group: Group, permission: Permission) -> None:
    if permission not in group.permissions:
        group.permissions.append(permission)
        db.add(group)
        db.commit()


def remove_permission_from_group(db: Session, group: Group, permission: Permission) -> None:
    if permission in group.permissions:
        group.permissions.remove(permission)
        db.add(group)
        db.commit()


def get_user(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def add_user_to_group(db: Session, group: Group, user: User) -> None:
    if user not in group.users:
        group.users.append(user)
        db.add(group)
        db.commit()


def remove_user_from_group(db: Session, group: Group, user: User) -> None:
    if user in group.users:
        group.users.remove(user)
        db.add(group)
        db.commit()
