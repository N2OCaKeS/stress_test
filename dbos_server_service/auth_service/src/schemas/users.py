"""Schemas for user CRUD and role assignments."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class InitialRoleAssignment(BaseModel):
    service_name: str
    roles: list[str]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8)
    email: EmailStr | None = None
    # account_admin users have no department; required for all other roles
    department_id: str | None = None
    platform_role: str | None = None
    initial_roles: list[InitialRoleAssignment] | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    department_id: str | None = None
    status: str | None = None
    platform_role: str | None = None


class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str | None
    department_id: str | None
    department_name: str | None
    status: str
    platform_role: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AssignRolesRequest(BaseModel):
    service_name: str
    roles: list[str]


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class BanRequest(BaseModel):
    reason: str | None = None
    ban_type: str = "permanent"
    expires_at: datetime | None = None
