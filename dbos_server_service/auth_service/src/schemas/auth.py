"""Schemas for login, refresh, logout, and identity."""

from pydantic import BaseModel, Field


class IdentityContext(BaseModel):
    user_id: str
    username: str
    department_id: str | None = None
    department_name: str | None = None
    allowed_services: list[str] = Field(default_factory=list)
    service_roles: dict[str, list[str]] = Field(default_factory=dict)
    is_banned: bool = False
    platform_role: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    identity: IdentityContext


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class LogoutRequest(BaseModel):
    refresh_token: str
