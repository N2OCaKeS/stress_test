from datetime import datetime

from pydantic import BaseModel, Field


class IntegrationCapabilities(BaseModel):
    docker_pull: bool = True
    docker_push: bool
    portainer_access: bool
    devpi_read: bool = True
    devpi_write: bool


class IntegrationIdentity(BaseModel):
    id: int
    login: str
    role: str
    permissions: list[str] = Field(default_factory=list)
    capabilities: IntegrationCapabilities


class RegistryAccessEntry(BaseModel):
    type: str = Field(..., example="repository")
    name: str = Field(..., example="library/alpine")
    actions: list[str] = Field(default_factory=list, example=["pull", "push"])


class RegistryTokenResponse(BaseModel):
    token: str
    access_token: str
    expires_in: int
    issued_at: datetime
    access: list[RegistryAccessEntry] = Field(default_factory=list)


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = "profile"
    id_token: str | None = None


class OAuthUserInfoResponse(BaseModel):
    sub: str
    id: int
    login: str
    username: str
    preferred_username: str
    name: str
    role: str
    permissions: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    capabilities: IntegrationCapabilities


# Backward-compatible aliases for old code imports.
PortainerOAuthTokenResponse = OAuthTokenResponse
PortainerOAuthResourceResponse = OAuthUserInfoResponse
