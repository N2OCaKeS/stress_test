"""Schemas for OAuth2 client management and token flows."""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class OAuthClientCreate(BaseModel):
    name: str
    description: str | None = None
    department_id: str
    redirect_uris: list[str] = Field(default_factory=list)
    allowed_scopes: list[str] = Field(default_factory=list)
    grant_types: list[str] = Field(default=["authorization_code"])


class OAuthClientResponse(BaseModel):
    id: str
    client_id: str
    department_id: str
    name: str
    description: str | None
    redirect_uris: list[str]
    allowed_scopes: list[str]
    grant_types: list[str]
    is_active: bool
    created_at: datetime


class OAuthClientCreatedResponse(OAuthClientResponse):
    """Returned only on creation — includes the plaintext secret (shown once)."""
    client_secret: str


# ── Authorization code flow ───────────────────────────────────────────────────

class OAuthAuthorizeRequest(BaseModel):
    response_type: str = "code"
    client_id: str
    redirect_uri: str
    scope: str = ""
    state: str | None = None


class OAuthTokenRequest(BaseModel):
    grant_type: str
    # authorization_code fields
    code: str | None = None
    redirect_uri: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    # client_credentials fields (client_id/secret same as above)


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = ""
