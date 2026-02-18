from datetime import datetime

from pydantic import BaseModel, Field


class OAuthClientBase(BaseModel):
    display_name: str = Field(..., example="Portainer")
    description: str | None = Field(default=None, example="OAuth client for Portainer")
    redirect_uri_prefixes: list[str] = Field(
        default_factory=list,
        example=["https://allta.devos.astralinux.ru:9443"],
    )
    required_permission: str | None = Field(default=None, example="portainer")
    default_scope: str = Field(default="profile", example="profile")
    enabled: bool = Field(default=True)


class OAuthClientCreate(OAuthClientBase):
    client_id: str = Field(..., example="allta-portainer")
    client_secret: str = Field(..., example="change-me")


class OAuthClientUpdate(BaseModel):
    client_secret: str | None = Field(default=None, example="new-secret")
    display_name: str | None = Field(default=None, example="Flower")
    description: str | None = Field(default=None, example="OAuth client for Flower")
    redirect_uri_prefixes: list[str] | None = Field(
        default=None,
        example=["https://allta.devos.astralinux.ru:5555/oauth2/callback"],
    )
    required_permission: str | None = Field(default=None, example="flower")
    default_scope: str | None = Field(default=None, example="profile")
    enabled: bool | None = Field(default=None)


class OAuthClientRead(OAuthClientBase):
    id: int
    client_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
