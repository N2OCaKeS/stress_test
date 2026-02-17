import base64
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from jose import jwt
from sqlalchemy.orm import Session

from app.api.v1.models.access_control import (
    PERMISSION_DEVPI,
    PERMISSION_DOCKER,
    PERMISSION_PORTAINER,
)
from app.api.v1.crud.user import get_user_by_login
from app.api.v1.dependencies import get_current_user, resolve_user_from_token
from app.api.v1.models.user import User
from app.api.v1.schemas.integration import (
    IntegrationCapabilities,
    IntegrationIdentity,
    RegistryAccessEntry,
    RegistryTokenResponse,
)
from app.db.session import get_db
from app.utils.config import settings
from app.utils.security import verify_password


router = APIRouter(
    prefix="/integrations",
    tags=["Integrations"],
)


_BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Allta Auth"'}
_REGISTRY_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Docker Registry"'}


def _decode_basic_credentials(encoded_credentials: str) -> tuple[str, str]:
    try:
        raw = base64.b64decode(encoded_credentials).decode("utf-8")
    except Exception:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid basic auth encoding",
            headers=_BASIC_CHALLENGE,
        )

    if ":" not in raw:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid basic auth payload",
            headers=_BASIC_CHALLENGE,
        )
    return raw.split(":", 1)


def _authenticate_basic(encoded_credentials: str, db: Session) -> User:
    login, password = _decode_basic_credentials(encoded_credentials)
    user = get_user_by_login(db, login)
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers=_BASIC_CHALLENGE,
        )
    return user


def _resolve_request_user(
    request: Request,
    db: Session,
    *,
    allow_anonymous: bool,
) -> User | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        if allow_anonymous:
            return None
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers=_BASIC_CHALLENGE,
        )

    scheme, _, credentials = authorization.partition(" ")
    if not scheme or not credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Malformed Authorization header",
            headers=_BASIC_CHALLENGE,
        )

    scheme = scheme.lower()
    if scheme == "basic":
        return _authenticate_basic(credentials, db)
    if scheme == "bearer":
        return resolve_user_from_token(credentials, db)

    if allow_anonymous:
        return None
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Unsupported authorization scheme",
        headers=_BASIC_CHALLENGE,
    )


def _parse_registry_scope(raw_scope: str) -> RegistryAccessEntry | None:
    parts = raw_scope.split(":", 2)
    if len(parts) != 3:
        return None
    resource_type, name, raw_actions = parts
    actions = [action.strip() for action in raw_actions.split(",") if action.strip()]
    return RegistryAccessEntry(type=resource_type, name=name, actions=actions)


def _filter_registry_actions(
    requested_actions: list[str],
    user: User | None,
) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    denied: list[str] = []
    for action in requested_actions:
        if action == "pull":
            if user is None and not settings.REGISTRY_ALLOW_ANON_PULL:
                denied.append(action)
            else:
                allowed.append(action)
            continue

        is_write_action = action in {"push", "delete", "*"}
        if is_write_action:
            if user is None:
                denied.append(action)
                continue
            if settings.REGISTRY_PUSH_REQUIRES_ADMIN and not user.has_permission(
                PERMISSION_DOCKER
            ):
                denied.append(action)
                continue

        allowed.append(action)
    return allowed, denied


def _build_registry_access(
    scopes: list[str],
    user: User | None,
) -> list[RegistryAccessEntry]:
    access: list[RegistryAccessEntry] = []
    needs_auth = False
    forbidden_for_role = False

    for raw_scope in scopes:
        parsed = _parse_registry_scope(raw_scope)
        if not parsed:
            continue

        allowed_actions, denied_actions = _filter_registry_actions(parsed.actions, user)
        if denied_actions:
            if user is None:
                needs_auth = True
            else:
                forbidden_for_role = True

        if allowed_actions:
            access.append(
                RegistryAccessEntry(
                    type=parsed.type,
                    name=parsed.name,
                    actions=allowed_actions,
                )
            )

    if needs_auth:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for requested scope",
            headers=_REGISTRY_CHALLENGE,
        )
    if forbidden_for_role:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for requested write scopes",
        )
    return access


@lru_cache(maxsize=1)
def _load_registry_signing_key() -> str:
    if settings.REGISTRY_TOKEN_PRIVATE_KEY_PATH:
        key_path = Path(settings.REGISTRY_TOKEN_PRIVATE_KEY_PATH)
        try:
            return key_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read registry private key '{key_path}': {exc}"
            ) from exc
    return settings.REGISTRY_TOKEN_SECRET_KEY


def _build_identity(user: User) -> IntegrationIdentity:
    permissions = sorted(user.permission_codes())
    return IntegrationIdentity(
        id=user.id,
        login=user.login,
        role=user.role_name(),
        permissions=permissions,
        capabilities=IntegrationCapabilities(
            docker_pull=True,
            docker_push=(
                user.has_permission(PERMISSION_DOCKER)
                or not settings.REGISTRY_PUSH_REQUIRES_ADMIN
            ),
            portainer_access=user.has_permission(PERMISSION_PORTAINER),
            devpi_read=True,
            devpi_write=(
                user.has_permission(PERMISSION_DEVPI)
                or not settings.DEVPI_WRITE_REQUIRES_ADMIN
            ),
        ),
    )


def _set_identity_headers(response: Response, user: User) -> None:
    response.headers["X-Auth-User"] = user.login
    response.headers["X-Auth-User-Id"] = str(user.id)
    response.headers["X-Auth-Role"] = user.role_name()


@router.get(
    "/registry/token",
    response_model=RegistryTokenResponse,
    summary="Docker Registry token endpoint",
)
def issue_registry_token(
    request: Request,
    service: str | None = Query(default=None, description="Registry service name"),
    scope: list[str] = Query(default=[]),
    account: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Совместимый endpoint для Docker Registry token auth.
    - Анонимные запросы могут получить pull-scope (если разрешено в конфиге).
    - Push/delete требуют право docker (role/group based).
    """
    user = _resolve_request_user(request, db, allow_anonymous=True)
    if account and user and account != user.login:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Account does not match authenticated user",
            headers=_REGISTRY_CHALLENGE,
        )

    access = _build_registry_access(scope, user)
    now = datetime.now(timezone.utc)
    expires_in = max(settings.REGISTRY_TOKEN_EXPIRE_SECONDS, 60)
    claims = {
        "iss": settings.REGISTRY_TOKEN_ISSUER,
        "sub": user.login if user else "anonymous",
        "aud": service or settings.REGISTRY_TOKEN_SERVICE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid4()),
        "access": [entry.model_dump() for entry in access],
    }
    if user:
        claims["user_id"] = user.id
        claims["role"] = user.role_name()

    try:
        registry_key = _load_registry_signing_key()
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    token = jwt.encode(
        claims,
        registry_key,
        algorithm=settings.REGISTRY_TOKEN_ALGORITHM,
    )
    return RegistryTokenResponse(
        token=token,
        access_token=token,
        expires_in=expires_in,
        issued_at=now,
        access=access,
    )


@router.get(
    "/whoami",
    response_model=IntegrationIdentity,
    summary="Identity and integration capabilities",
)
def whoami(current_user: User = Depends(get_current_user)):
    return _build_identity(current_user)


@router.get(
    "/basic/verify",
    response_model=IntegrationIdentity,
    summary="Verify credentials for reverse-proxy integrations",
)
def verify_basic_for_integrations(
    request: Request,
    response: Response,
    require_admin: bool = Query(default=False),
    permission: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Поддерживает Basic/Bearer и возвращает профиль пользователя.
    Удобно для nginx auth_request перед DevPI/Portainer/другими сервисами.
    """
    user = _resolve_request_user(request, db, allow_anonymous=False)
    if require_admin and not user.is_admin_effective():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    if permission and not user.has_permission(permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions: '{permission}' required",
        )

    _set_identity_headers(response, user)
    return _build_identity(user)


@router.get(
    "/devpi/authorize",
    response_model=IntegrationIdentity,
    summary="Authorize DevPI action",
)
def authorize_devpi_action(
    request: Request,
    response: Response,
    action: str = Query(default="read", description="read|write|upload|delete"),
    db: Session = Depends(get_db),
):
    user = _resolve_request_user(request, db, allow_anonymous=False)
    normalized_action = action.strip().lower()
    is_write_action = normalized_action in {"write", "upload", "delete", "push"}
    if (
        is_write_action
        and settings.DEVPI_WRITE_REQUIRES_ADMIN
        and not user.has_permission(PERMISSION_DEVPI)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for DevPI write actions",
        )

    _set_identity_headers(response, user)
    return _build_identity(user)


@router.get(
    "/portainer/authorize",
    response_model=IntegrationIdentity,
    summary="Authorize Portainer access",
)
def authorize_portainer(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = _resolve_request_user(request, db, allow_anonymous=False)
    if not user.has_permission(PERMISSION_PORTAINER):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for Portainer access",
        )
    _set_identity_headers(response, user)
    return _build_identity(user)
