from typing import List

from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.orm import Session

from app.api.v1.crud import access_control as ac
from app.api.v1.dependencies import get_current_admin_user
from app.api.v1.schemas.access_control import (
    GroupCreate,
    GroupRead,
    GroupUpdate,
    PermissionCreate,
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)
from app.db.session import get_db


router = APIRouter(
    prefix="/admin/access",
    tags=[],
)


def _role_to_read(role) -> RoleRead:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=sorted([perm.code for perm in role.permissions]),
    )


def _group_to_read(group) -> GroupRead:
    return GroupRead(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=sorted([perm.code for perm in group.permissions]),
        users_count=len(group.users),
    )


@router.get(
    "/permissions",
    response_model=List[PermissionRead],
    summary="Список прав (admin)",
    tags=["Роли и права"],
)
def admin_list_permissions(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return ac.list_permissions(db)


@router.post(
    "/permissions",
    response_model=PermissionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать право (admin)",
    tags=["Роли и права"],
)
def admin_create_permission(
    body: PermissionCreate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return ac.create_permission(db, body.code, body.description)


@router.get(
    "/roles",
    response_model=List[RoleRead],
    summary="Список ролей (admin)",
    tags=["Роли и права"],
)
def admin_list_roles(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return [_role_to_read(role) for role in ac.list_roles(db)]


@router.post(
    "/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать роль (admin)",
    tags=["Роли и права"],
)
def admin_create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return _role_to_read(ac.create_role(db, body.name, body.description))


@router.patch(
    "/roles/{role_id}",
    response_model=RoleRead,
    summary="Обновить роль (admin)",
    tags=["Роли и права"],
)
def admin_update_role(
    role_id: int,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    role = ac.update_role(db, role_id, name=body.name, description=body.description)
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return _role_to_read(role)


@router.post(
    "/roles/{role_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Назначить право роли (admin)",
    tags=["Роли и права"],
)
def admin_add_permission_to_role(
    role_id: int,
    permission_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    role = ac.get_role(db, role_id)
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    permission = ac.get_permission(db, permission_id)
    if not permission:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permission not found")
    ac.add_permission_to_role(db, role, permission)


@router.delete(
    "/roles/{role_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать право у роли (admin)",
    tags=["Роли и права"],
)
def admin_remove_permission_from_role(
    role_id: int,
    permission_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    role = ac.get_role(db, role_id)
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    permission = ac.get_permission(db, permission_id)
    if not permission:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permission not found")
    ac.remove_permission_from_role(db, role, permission)


@router.get(
    "/groups",
    response_model=List[GroupRead],
    summary="Список групп (admin)",
    tags=["Группы"],
)
def admin_list_groups(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return [_group_to_read(group) for group in ac.list_groups(db)]


@router.post(
    "/groups",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать группу (admin)",
    tags=["Группы"],
)
def admin_create_group(
    body: GroupCreate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return _group_to_read(ac.create_group(db, body.name, body.description))


@router.patch(
    "/groups/{group_id}",
    response_model=GroupRead,
    summary="Обновить группу (admin)",
    tags=["Группы"],
)
def admin_update_group(
    group_id: int,
    body: GroupUpdate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    group = ac.update_group(db, group_id, name=body.name, description=body.description)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return _group_to_read(group)


@router.post(
    "/groups/{group_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Назначить право группе (admin)",
    tags=["Группы"],
)
def admin_add_permission_to_group(
    group_id: int,
    permission_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    group = ac.get_group(db, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    permission = ac.get_permission(db, permission_id)
    if not permission:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permission not found")
    ac.add_permission_to_group(db, group, permission)


@router.delete(
    "/groups/{group_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать право у группы (admin)",
    tags=["Группы"],
)
def admin_remove_permission_from_group(
    group_id: int,
    permission_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    group = ac.get_group(db, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    permission = ac.get_permission(db, permission_id)
    if not permission:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permission not found")
    ac.remove_permission_from_group(db, group, permission)


@router.post(
    "/groups/{group_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Добавить пользователя в группу (admin)",
    tags=["Группы"],
)
def admin_add_user_to_group(
    group_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    group = ac.get_group(db, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    user = ac.get_user(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    ac.add_user_to_group(db, group, user)


@router.delete(
    "/groups/{group_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить пользователя из группы (admin)",
    tags=["Группы"],
)
def admin_remove_user_from_group(
    group_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    group = ac.get_group(db, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    user = ac.get_user(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    ac.remove_user_from_group(db, group, user)
