"""Schemas for user groups."""

from datetime import datetime

from pydantic import BaseModel


class GroupCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None


class GroupUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class GroupResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime
    created_by: str | None

    model_config = {"from_attributes": True}


class MemberAddRequest(BaseModel):
    user_id: str


class MemberResponse(BaseModel):
    user_id: str
    username: str
    added_at: datetime


class GroupServiceGrantRequest(BaseModel):
    service_name: str


class GroupServiceAccessResponse(BaseModel):
    service_name: str
    is_active: bool
    granted_at: datetime
    granted_by: str | None

    model_config = {"from_attributes": True}


class GroupRoleAssignRequest(BaseModel):
    service_name: str
    roles: list[str]


class GroupRoleResponse(BaseModel):
    service_name: str
    roles: list[str]


class UserGroupsResponse(BaseModel):
    group_id: str
    group_name: str
    display_name: str
    added_at: datetime
