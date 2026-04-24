"""Schemas for Docker registry token auth protocol and config management."""

from datetime import datetime

from pydantic import BaseModel, Field


PULL_POLICY_ALL = "all"
PULL_POLICY_RESTRICTED = "restricted"


class DockerRegistryConfigCreate(BaseModel):
    """Create or replace the Docker registry config for a department."""
    pull_policy: str = Field(default=PULL_POLICY_ALL, pattern="^(all|restricted)$")
    # Required when pull_policy = "restricted"; ignored for "all"
    pull_user_ids: list[str] = Field(default_factory=list)
    push_user_ids: list[str] = Field(default_factory=list)


class DockerRegistryConfigUpdate(BaseModel):
    pull_policy: str | None = Field(default=None, pattern="^(all|restricted)$")
    pull_user_ids: list[str] | None = None
    push_user_ids: list[str] | None = None
    is_enabled: bool | None = None


class DockerRegistryConfigResponse(BaseModel):
    department_id: str
    is_enabled: bool
    pull_policy: str
    pull_user_ids: list[str]
    push_user_ids: list[str]
    created_at: datetime
    updated_at: datetime


class DockerTokenResponse(BaseModel):
    token: str
    access_token: str  # Docker accepts either field name
    expires_in: int
    issued_at: str
