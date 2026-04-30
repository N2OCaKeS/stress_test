"""User management endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, AnyAdmin, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.groups import UserGroupsResponse
from src.schemas.users import AddUserToGroupRequest, AssignRolesRequest, BanRequest, ResetPasswordRequest, UserCreate, UserResponse, UserUpdate
from src.services import group_service, user_service

router = APIRouter(prefix="/users")


@router.get("", response_model=list[UserResponse])
async def list_users(
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    return await user_service.list_users(
        db=db,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/department/{department_id}", response_model=list[UserResponse])
async def list_users_by_department(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    return await user_service.list_users_by_department(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_service.create_user(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        username=body.username,
        password=body.password,
        department_id=body.department_id,
        email=body.email,
        platform_role=body.platform_role,
        initial_roles=body.initial_roles,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_service.update_user(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        user_id=user_id,
        updates=body.model_dump(exclude_none=True),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{user_id}/roles")
async def assign_roles(
    user_id: str,
    body: AssignRolesRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await user_service.assign_roles(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        user_id=user_id,
        service_name=body.service_name,
        roles=body.roles,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post("/{user_id}/reset-password", response_model=OkResponse)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await user_service.reset_password(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        new_password=body.new_password,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post("/{user_id}/ban", response_model=OkResponse)
async def ban_user(
    user_id: str,
    body: BanRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await user_service.ban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        ban_type=body.ban_type,
        reason=body.reason,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.get("/{user_id}/groups", response_model=list[UserGroupsResponse])
async def list_user_groups(
    user_id: str,
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[UserGroupsResponse]:
    return await group_service.list_user_groups(
        db=db,
        identity=identity,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{user_id}/groups", response_model=OkResponse, status_code=201)
async def add_user_to_group(
    user_id: str,
    body: AddUserToGroupRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.add_member(
        db=db,
        identity=identity,
        group_id=body.group_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.delete("/{user_id}/groups/{group_id}", response_model=OkResponse)
async def remove_user_from_group(
    user_id: str,
    group_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.remove_member(
        db=db,
        identity=identity,
        group_id=group_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.post("/{user_id}/unban", response_model=OkResponse)
async def unban_user(
    user_id: str,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await user_service.unban_user(
        db=db,
        actor_id=identity.user_id,
        user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
