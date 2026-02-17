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
