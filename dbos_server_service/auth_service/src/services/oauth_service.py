"""OAuth2 client management and token flow workflows."""

import hmac
import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, NotFoundError
from src.core.security import create_access_token, hash_opaque_token
from src.repositories.departments import DepartmentRepository
from src.repositories.oauth_clients import OAuthClientRepository, OAuthCodeRepository
from src.repositories.roles import RoleRepository
from src.repositories.users import UserRepository
from src.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreatedResponse,
    OAuthClientResponse,
    OAuthTokenResponse,
)
from src.services import audit_service
from src.utils.time import is_expired, utcnow

_SECRET_PREFIX_LEN = 12


def _to_response(client) -> OAuthClientResponse:
    return OAuthClientResponse(
        id=client.id,
        client_id=client.client_id,
        department_id=client.department_id,
        name=client.name,
        description=client.description,
        redirect_uris=client.redirect_uris,
        allowed_scopes=client.allowed_scopes,
        grant_types=client.grant_types,
        is_active=client.is_active,
        created_at=client.created_at,
    )


async def create_client(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    data: OAuthClientCreate,
    request_id: str | None = None,
) -> OAuthClientCreatedResponse:
    dept_repo = DepartmentRepository(db)
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != data.department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only create OAuth2 clients for their own department",
            )

    dept = await dept_repo.get_by_id(data.department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    if await client_repo.exists_name(data.department_id, data.name):
        raise ConflictError(
            error_code="OAUTH_CLIENT_NAME_EXISTS",
            message=f"OAuth2 client '{data.name}' already exists in this department",
        )

    raw_secret = f"cs_{secrets.token_urlsafe(32)}"
    secret_hash = hash_opaque_token(raw_secret)
    secret_prefix = raw_secret[:_SECRET_PREFIX_LEN]

    client = await client_repo.create(
        department_id=data.department_id,
        name=data.name,
        client_secret_hash=secret_hash,
        client_secret_prefix=secret_prefix,
        redirect_uris=data.redirect_uris,
        allowed_scopes=data.allowed_scopes,
        grant_types=data.grant_types,
        description=data.description,
        created_by=actor_id,
    )
    await db.commit()
    audit_service.emit("oauth_client.create", actor_id, target_id=client.id, target_type="oauth_client", request_id=request_id)
    return OAuthClientCreatedResponse(**_to_response(client).model_dump(), client_secret=raw_secret)


async def list_clients(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str | None = None,
    request_id: str | None = None,
) -> list[OAuthClientResponse]:
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        dept_id = actor.department_id if actor else None
        clients = await client_repo.list_by_department(dept_id) if dept_id else []
    elif department_id:
        clients = await client_repo.list_by_department(department_id)
    else:
        clients = await client_repo.list_all()

    audit_service.emit("oauth_client.list", actor_id, status="success", allowed=True, request_id=request_id)
    return [_to_response(c) for c in clients]


async def delete_client(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    client_db_id: str,
    request_id: str | None = None,
) -> None:
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    client = await client_repo.get_by_id(client_db_id)
    if client is None:
        raise NotFoundError(error_code="OAUTH_CLIENT_NOT_FOUND", message="OAuth2 client not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != client.department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="Cannot delete OAuth2 client outside your department",
            )

    await client_repo.deactivate(client)
    await db.commit()
    audit_service.emit("oauth_client.delete", actor_id, target_id=client_db_id, target_type="oauth_client", request_id=request_id)


# ── Authorization code flow ───────────────────────────────────────────────────

async def issue_authorization_code(
    db: AsyncSession,
    client_id: str,
    user_id: str,
    redirect_uri: str,
    scopes: list[str],
    request_id: str | None = None,
) -> str:
    """Validate the client, then create and return a raw authorization code."""
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Unknown or inactive OAuth2 client")

    if "authorization_code" not in client.grant_types:
        raise AuthorizationError(error_code="GRANT_TYPE_NOT_ALLOWED", message="authorization_code grant not enabled for this client")

    if redirect_uri not in client.redirect_uris:
        raise AuthorizationError(error_code="REDIRECT_URI_MISMATCH", message="redirect_uri does not match registered URIs")

    effective_scopes = [s for s in scopes if s in client.allowed_scopes]

    raw_code = secrets.token_urlsafe(32)
    code_hash = hash_opaque_token(raw_code)
    settings = get_settings()
    expires_at = utcnow() + timedelta(seconds=settings.oauth_code_ttl_seconds)

    code_repo = OAuthCodeRepository(db)
    await code_repo.create(
        client_id=client_id,
        user_id=user_id,
        code_hash=code_hash,
        redirect_uri=redirect_uri,
        scopes=effective_scopes,
        expires_at=expires_at,
    )
    await db.commit()
    audit_service.emit(
        "oauth.authorization_code_issued",
        user_id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={"scopes": effective_scopes},
        request_id=request_id,
    )
    return raw_code


async def exchange_code(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    request_id: str | None = None,
) -> OAuthTokenResponse:
    """Exchange authorization code for an access token."""
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if not hmac.compare_digest(hash_opaque_token(client_secret), client.client_secret_hash):
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    code_hash = hash_opaque_token(code)
    code_repo = OAuthCodeRepository(db)
    auth_code = await code_repo.get_by_hash(code_hash)
    if auth_code is None:
        raise AuthenticationError(error_code="OAUTH_CODE_INVALID", message="Authorization code is invalid or already used")

    if is_expired(auth_code.expires_at):
        raise AuthenticationError(error_code="OAUTH_CODE_EXPIRED", message="Authorization code has expired")

    if auth_code.redirect_uri != redirect_uri:
        raise AuthorizationError(error_code="REDIRECT_URI_MISMATCH", message="redirect_uri mismatch")

    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    user_repo = UserRepository(db)

    user = await user_repo.get_by_id(auth_code.user_id)
    if user is None:
        raise AuthenticationError(error_code="OAUTH_USER_NOT_FOUND", message="User no longer exists")

    allowed = await dept_repo.list_active_services(user.department_id) if user.department_id else []
    roles = await role_repo.get_all_roles(user.id)
    scoped_roles = {k: v for k, v in roles.items() if k in allowed and k in auth_code.scopes}

    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    access_token = create_access_token(
        payload={
            "sub": user.id,
            "username": user.username,
            "department_id": user.department_id,
            "platform_role": user.platform_role,
            "allowed_services": [s for s in allowed if s in auth_code.scopes],
            "service_roles": scoped_roles,
            "oauth_client_id": client_id,
        },
        expires_delta=ttl,
    )
    await code_repo.mark_used(auth_code)
    await db.commit()
    audit_service.emit(
        "oauth.code_exchanged",
        user.id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={"scopes": auth_code.scopes},
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(auth_code.scopes),
    )


async def client_credentials_token(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    request_id: str | None = None,
) -> OAuthTokenResponse:
    """Issue an access token for client_credentials grant (machine-to-machine)."""
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if not hmac.compare_digest(hash_opaque_token(client_secret), client.client_secret_hash):
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if "client_credentials" not in client.grant_types:
        raise AuthorizationError(error_code="GRANT_TYPE_NOT_ALLOWED", message="client_credentials grant not enabled for this client")

    dept_repo = DepartmentRepository(db)
    allowed = await dept_repo.list_active_services(client.department_id)
    effective = [s for s in allowed if s in client.allowed_scopes]

    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    access_token = create_access_token(
        payload={
            "sub": client.client_id,
            "department_id": client.department_id,
            "allowed_services": effective,
            "service_roles": {},
            "oauth_client_id": client_id,
        },
        expires_delta=ttl,
    )
    audit_service.emit(
        "oauth.client_credentials_token",
        client.id,
        actor_type="service",
        department_id=client.department_id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={"scopes": effective},
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(effective),
    )
