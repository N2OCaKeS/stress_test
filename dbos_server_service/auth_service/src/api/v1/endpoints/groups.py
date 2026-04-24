"""User group management endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AnyAdmin, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.groups import (
    GroupCreate, GroupResponse, GroupRoleAssignRequest, GroupRoleResponse,
    GroupServiceAccessResponse, GroupServiceGrantRequest, GroupUpdate,
    MemberAddRequest, MemberResponse, UserGroupsResponse,
)
from src.services import group_service

router = APIRouter()

# ── Groups CRUD ───────────────────────────────────────────────────────────────

groups_router = APIRouter(prefix="/groups")


@groups_router.get("", response_model=list[GroupResponse])
async def list_groups(
    request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> list[GroupResponse]:
    return await group_service.list_groups(db, identity, request_id=getattr(request.state, "request_id", None))


@groups_router.post("", response_model=GroupResponse, status_code=201)
async def create_group(
    body: GroupCreate, request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    return await group_service.create_group(
        db, identity, body.name, body.display_name, body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: str, body: GroupUpdate, request: Request,
    identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    return await group_service.update_group(
        db, identity, group_id, body.display_name, body.description,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete("/{group_id}", response_model=OkResponse)
async def delete_group(
    group_id: str, request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.delete_group(db, identity, group_id,
                                     request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Members ───────────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/members", response_model=list[MemberResponse])
async def list_members(
    group_id: str, request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> list[MemberResponse]:
    return await group_service.list_members(db, identity, group_id,
                                            request_id=getattr(request.state, "request_id", None))


@groups_router.post("/{group_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    group_id: str, body: MemberAddRequest, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> MemberResponse:
    return await group_service.add_member(db, identity, group_id, body.user_id,
                                          request_id=getattr(request.state, "request_id", None))


@groups_router.delete("/{group_id}/members/{user_id}", response_model=OkResponse)
async def remove_member(
    group_id: str, user_id: str, request: Request,
    identity: AnyAdmin, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.remove_member(db, identity, group_id, user_id,
                                      request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Group service access ──────────────────────────────────────────────────────

@groups_router.get("/{group_id}/services", response_model=list[GroupServiceAccessResponse])
async def list_group_services(
    group_id: str, request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> list[GroupServiceAccessResponse]:
    return await group_service.list_group_services(db, identity, group_id,
                                                   request_id=getattr(request.state, "request_id", None))


@groups_router.post("/{group_id}/services", response_model=GroupServiceAccessResponse, status_code=201)
async def grant_service(
    group_id: str, body: GroupServiceGrantRequest, request: Request,
    identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> GroupServiceAccessResponse:
    return await group_service.grant_service_to_group(
        db, identity, group_id, body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete("/{group_id}/services/{service_name}", response_model=OkResponse)
async def revoke_service(
    group_id: str, service_name: str, request: Request,
    identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.revoke_service_from_group(db, identity, group_id, service_name,
                                                  request_id=getattr(request.state, "request_id", None))
    return OkResponse()


# ── Group service roles ───────────────────────────────────────────────────────

@groups_router.get("/{group_id}/roles", response_model=list[GroupRoleResponse])
async def list_group_roles(
    group_id: str, request: Request, identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> list[GroupRoleResponse]:
    return await group_service.list_group_roles(db, identity, group_id,
                                                request_id=getattr(request.state, "request_id", None))


@groups_router.post("/{group_id}/roles", response_model=GroupRoleResponse, status_code=201)
async def assign_group_roles(
    group_id: str, body: GroupRoleAssignRequest, request: Request,
    identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> GroupRoleResponse:
    return await group_service.assign_group_roles(
        db, identity, group_id, body.service_name, body.roles,
        request_id=getattr(request.state, "request_id", None),
    )


@groups_router.delete("/{group_id}/roles/{service_name}", response_model=OkResponse)
async def revoke_group_roles(
    group_id: str, service_name: str, request: Request,
    identity: CurrentIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await group_service.revoke_group_roles(db, identity, group_id, service_name,
                                           request_id=getattr(request.state, "request_id", None))
    return OkResponse()


router.include_router(groups_router, tags=["groups"])
