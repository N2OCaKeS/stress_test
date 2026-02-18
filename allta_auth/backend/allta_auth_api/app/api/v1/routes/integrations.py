import base64
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.api.v1.crud.oauth_client import get_oauth_client_by_client_id
from app.api.v1.crud.token import create_active_token
from app.api.v1.crud.user import get_user, get_user_by_login
from app.api.v1.dependencies import get_current_user, resolve_user_from_token
from app.api.v1.models.access_control import (
    PERMISSION_DEVPI,
    PERMISSION_DOCKER,
    PERMISSION_PORTAINER,
)
from app.api.v1.models.oauth_client import OAuthClient
from app.api.v1.models.user import User
from app.api.v1.schemas.integration import (
    IntegrationCapabilities,
    IntegrationIdentity,
    OAuthTokenResponse,
    OAuthUserInfoResponse,
    RegistryAccessEntry,
    RegistryTokenResponse,
)
from app.db.session import get_db
from app.utils.config import settings
from app.utils.security import verify_password


router = APIRouter(
    prefix="/integrations",
    tags=[],
)


_BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Allta Auth"'}
_REGISTRY_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Docker Registry"'}
_OAUTH_CLIENT_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Allta OAuth Client"'}
_OAUTH_ACCESS_CHALLENGE = {"WWW-Authenticate": "Bearer"}
_OAUTH_AUDIENCE = "allta-oauth"
_OAUTH_CODE_TYPE = "oauth_code"
_OAUTH_ACCESS_TYPE = "oauth_access"
_OAUTH_NGINX_STATE_TYPE = "oauth_nginx_state"


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
        cookie_token = request.cookies.get("access_token")
        if cookie_token:
            try:
                return resolve_user_from_token(cookie_token, db)
            except HTTPException:
                # Invalid/expired cookie should fall back to regular auth logic.
                pass
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
    *,
    resource_type: str,
    resource_name: str,
) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    denied: list[str] = []

    def allow(action_value: str) -> None:
        if action_value not in allowed:
            allowed.append(action_value)

    for action in requested_actions:
        if action == "pull":
            if user is None and not settings.REGISTRY_ALLOW_ANON_PULL:
                denied.append(action)
            else:
                allow(action)
            continue

        if action == "*":
            # Registry UI requests registry:catalog:* for listing repositories.
            # Keep it readable for regular authenticated users (and optional anon pull mode),
            # but do not grant repository write rights.
            is_catalog_scope = resource_type == "registry" and resource_name == "catalog"
            if is_catalog_scope:
                if user is None and not settings.REGISTRY_ALLOW_ANON_PULL:
                    denied.append(action)
                else:
                    allow(action)
                continue

            if user is None:
                if settings.REGISTRY_ALLOW_ANON_PULL:
                    allow("pull")
                else:
                    denied.append(action)
                continue

            if settings.REGISTRY_PUSH_REQUIRES_ADMIN and not user.has_permission(
                PERMISSION_DOCKER
            ):
                # Downgrade repository '*' to read-only access for non-docker users.
                allow("pull")
                continue

            allow(action)
            continue

        is_write_action = action in {"push", "delete"}
        if is_write_action:
            if user is None:
                denied.append(action)
                continue
            if settings.REGISTRY_PUSH_REQUIRES_ADMIN and not user.has_permission(
                PERMISSION_DOCKER
            ):
                denied.append(action)
                continue

        allow(action)
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

        allowed_actions, denied_actions = _filter_registry_actions(
            parsed.actions,
            user,
            resource_type=parsed.type,
            resource_name=parsed.name,
        )
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


def _extract_x5c_chain_from_pem(pem_text: str) -> list[str]:
    begin_marker = "-----BEGIN CERTIFICATE-----"
    end_marker = "-----END CERTIFICATE-----"
    certificates: list[str] = []
    start = 0

    while True:
        begin = pem_text.find(begin_marker, start)
        if begin < 0:
            break
        end = pem_text.find(end_marker, begin)
        if end < 0:
            break

        body = pem_text[begin + len(begin_marker) : end]
        normalized = "".join(body.split())
        if normalized:
            certificates.append(normalized)
        start = end + len(end_marker)

    return certificates


@lru_cache(maxsize=1)
def _load_registry_x5c_chain() -> list[str]:
    cert_path_raw = settings.REGISTRY_TOKEN_CERT_PATH
    if not cert_path_raw:
        return []

    cert_path = Path(cert_path_raw)
    try:
        cert_content = cert_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read registry certificate '{cert_path}': {exc}"
        ) from exc

    x5c_chain = _extract_x5c_chain_from_pem(cert_content)
    if not x5c_chain:
        raise RuntimeError(
            f"Registry certificate '{cert_path}' does not contain PEM certificate data"
        )
    return x5c_chain


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


def _append_query_params(url: str, **params: str | None) -> str:
    filtered_params = {key: value for key, value in params.items() if value}
    if not filtered_params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(filtered_params)}"


def _is_allowed_oauth_redirect_uri(oauth_client: OAuthClient, redirect_uri: str) -> bool:
    allowed_prefixes = oauth_client.redirect_prefixes()
    if not allowed_prefixes:
        return False
    if "*" in allowed_prefixes:
        return True

    parsed_redirect = urlparse(redirect_uri)
    redirect_host = (parsed_redirect.hostname or "").lower()
    redirect_path = parsed_redirect.path or "/"

    for prefix in allowed_prefixes:
        if redirect_uri.startswith(prefix):
            return True

        parsed_prefix = urlparse(prefix)
        prefix_host = (parsed_prefix.hostname or "").lower()
        prefix_path = parsed_prefix.path or "/"

        if not parsed_prefix.scheme or not prefix_host:
            continue
        if parsed_prefix.scheme != parsed_redirect.scheme:
            continue
        if prefix_host != redirect_host:
            continue

        # Backward compatibility: allow redirect URI with explicit port
        # when stored prefix was saved without port.
        if parsed_prefix.port is None and redirect_path.startswith(prefix_path):
            return True
    return False


def _oauth_redirect_response(
    redirect_uri: str,
    *,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    return RedirectResponse(
        url=_append_query_params(
            redirect_uri,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        ),
        status_code=status.HTTP_302_FOUND,
    )


def _normalize_oauth_scope(scope: str | None, oauth_client: OAuthClient) -> str:
    normalized = (scope or oauth_client.default_scope or settings.OAUTH_DEFAULT_SCOPE).strip()
    return normalized or settings.OAUTH_DEFAULT_SCOPE


def _render_oauth_login_form(
    *,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str | None,
    service_name: str,
    username: str = "",
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    error_html = ""
    if error_message:
        error_html = "<div class='error-box'>" f"{escape(error_message)}" "</div>"

    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Allta Auth - Вход в {escape(service_name)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg-main: #070707;
      --bg-panel: #121212;
      --bg-input: #0e0e0e;
      --text-main: #f5f5f5;
      --text-muted: #c7c7c7;
      --accent: #ff7a00;
      --accent-hover: #ff8b1f;
      --accent-soft: #341900;
      --border: #3b3b3b;
      --border-accent: #7a3a00;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      color: var(--text-main);
      background:
        radial-gradient(circle at 10% 8%, #2f1800 0%, transparent 42%),
        radial-gradient(circle at 85% 92%, #261100 0%, transparent 36%),
        linear-gradient(155deg, #050505 0%, #0c0c0c 100%);
    }}

    main {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }}

    .card {{
      width: 100%;
      max-width: 440px;
      background: linear-gradient(180deg, #151515 0%, #101010 100%);
      border: 1px solid var(--border-accent);
      border-radius: 14px;
      padding: 22px;
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.58);
    }}

    .badge {{
      display: inline-block;
      margin-bottom: 10px;
      padding: 4px 9px;
      border-radius: 999px;
      border: 1px solid var(--border-accent);
      background: var(--accent-soft);
      color: #ffbf85;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}

    h1 {{
      margin: 0 0 8px 0;
      font-size: 24px;
      line-height: 1.2;
    }}

    .subtitle {{
      margin: 0 0 16px 0;
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.45;
    }}

    .error-box {{
      margin-bottom: 12px;
      padding: 10px;
      border: 1px solid var(--accent);
      background: #2a1400;
      color: #ffcf9f;
      border-radius: 9px;
      font-size: 14px;
    }}

    label {{
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      color: #dedede;
    }}

    input {{
      width: 100%;
      padding: 10px 12px;
      margin-bottom: 12px;
      border-radius: 9px;
      border: 1px solid var(--border);
      background: var(--bg-input);
      color: var(--text-main);
      outline: none;
    }}

    input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(255, 122, 0, 0.2);
    }}

    button {{
      width: 100%;
      margin-top: 4px;
      padding: 10px 12px;
      border: none;
      border-radius: 9px;
      background: var(--accent);
      color: #141414;
      font-weight: 700;
      cursor: pointer;
      transition: background-color .12s ease-in-out;
    }}

    button:hover {{
      background: var(--accent-hover);
    }}
  </style>
</head>
<body>
  <main>
    <section class="card">
      <div class="badge">Allta OAuth</div>
      <h1>Вход в {escape(service_name)}</h1>
      <p class="subtitle">
        Введите логин и пароль от Allta Auth, чтобы продолжить авторизацию.
      </p>
      {error_html}
      <form method="post" action="">
        <input type="hidden" name="response_type" value="{escape(response_type)}">
        <input type="hidden" name="client_id" value="{escape(client_id)}">
        <input type="hidden" name="redirect_uri" value="{escape(redirect_uri)}">
        <input type="hidden" name="scope" value="{escape(scope)}">
        <input type="hidden" name="state" value="{escape(state or '')}">
        <label for="username">Логин</label>
        <input id="username" name="username" type="text" required
          value="{escape(username)}">
        <label for="password">Пароль</label>
        <input id="password" name="password" type="password" required>
        <button type="submit">Войти</button>
      </form>
    </section>
  </main>
</body>
</html>
"""
    return HTMLResponse(content=html, status_code=status_code)


def _build_oauth_profile(user: User) -> OAuthUserInfoResponse:
    identity = _build_identity(user)
    return OAuthUserInfoResponse(
        sub=user.login,
        id=user.id,
        login=user.login,
        username=user.login,
        preferred_username=user.login,
        name=user.login,
        role=user.role_name(),
        permissions=identity.permissions,
        groups=sorted(group.name for group in user.groups if group.name),
        capabilities=identity.capabilities,
    )


def _build_oauth_code(
    user: User,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
) -> str:
    now = datetime.now(timezone.utc)
    expires_in = max(settings.OAUTH_CODE_EXPIRE_SECONDS, 30)
    claims = {
        "iss": settings.OAUTH_ISSUER,
        "aud": _OAUTH_AUDIENCE,
        "sub": user.login,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid4()),
        "typ": _OAUTH_CODE_TYPE,
        "user_id": user.id,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _decode_oauth_code(code: str) -> dict:
    try:
        claims = jwt.decode(
            code,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=_OAUTH_AUDIENCE,
        )
    except JWTError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code",
        )
    if claims.get("typ") != _OAUTH_CODE_TYPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid authorization code type",
        )
    return claims


def _resolve_oauth_client_credentials(
    request: Request,
    *,
    client_id: str | None,
    client_secret: str | None,
) -> tuple[str, str]:
    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "basic" and credentials:
            basic_client_id, basic_client_secret = _decode_basic_credentials(credentials)
            resolved_client_id = (basic_client_id or client_id or "").strip()
            resolved_client_secret = (basic_client_secret or client_secret or "").strip()
            return (resolved_client_id, resolved_client_secret)
    return ((client_id or "").strip(), (client_secret or "").strip())


def _validate_oauth_client_credentials(
    db: Session,
    *,
    client_id: str,
    client_secret: str,
) -> OAuthClient:
    oauth_client = get_oauth_client_by_client_id(db, client_id.strip())
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Unknown OAuth client id",
            headers=_OAUTH_CLIENT_CHALLENGE,
        )

    if not verify_password(client_secret, oauth_client.client_secret_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OAuth client secret",
            headers=_OAUTH_CLIENT_CHALLENGE,
        )
    return oauth_client


def _build_oauth_token(
    user: User,
    *,
    client_id: str,
    scope: str,
) -> OAuthTokenResponse:
    now = datetime.now(timezone.utc)
    expires_in = max(settings.OAUTH_TOKEN_EXPIRE_SECONDS, 60)
    permissions = sorted(user.permission_codes())
    groups = sorted(group.name for group in user.groups if group.name)

    access_claims = {
        "iss": settings.OAUTH_ISSUER,
        "aud": _OAUTH_AUDIENCE,
        "sub": user.login,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid4()),
        "typ": _OAUTH_ACCESS_TYPE,
        "user_id": user.id,
        "client_id": client_id,
        "scope": scope,
        "role": user.role_name(),
        "permissions": permissions,
        "groups": groups,
    }
    access_token = jwt.encode(
        access_claims,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    id_token_claims = {
        "iss": settings.OAUTH_ISSUER,
        "aud": client_id,
        "sub": user.login,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid4()),
        "preferred_username": user.login,
        "login": user.login,
        "role": user.role_name(),
        "permissions": permissions,
        "groups": groups,
    }
    id_token = jwt.encode(
        id_token_claims,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    return OAuthTokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=expires_in,
        scope=scope,
        id_token=id_token,
    )


def _build_browser_access_token(user: User, db: Session) -> str:
    token_entry = create_active_token(db, user.id)
    payload = {
        "jti": token_entry.jti,
        "user_id": user.id,
        "exp": int(token_entry.expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _expected_external_host() -> str:
    host = (settings.ALLTA_EXTERNAL_HOST or "").strip().lower()
    if not host:
        return "allta.devos.astralinux.ru"
    if ":" in host:
        return host.split(":", 1)[0]
    return host


def _is_allowed_redirect_target(rd: str) -> bool:
    parsed = urlparse(rd)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    return parsed.hostname.lower() == _expected_external_host()


def _build_oauth_nginx_state(
    *,
    client_id: str,
    redirect_uri: str,
    rd: str,
    scope: str,
) -> str:
    now = datetime.now(timezone.utc)
    expires_in = max(settings.OAUTH_CODE_EXPIRE_SECONDS, 30)
    claims = {
        "iss": settings.OAUTH_ISSUER,
        "aud": _OAUTH_AUDIENCE,
        "sub": client_id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid4()),
        "typ": _OAUTH_NGINX_STATE_TYPE,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "rd": rd,
        "scope": scope,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _decode_oauth_nginx_state(state: str) -> dict:
    try:
        claims = jwt.decode(
            state,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=_OAUTH_AUDIENCE,
        )
    except JWTError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth nginx state",
        )
    if claims.get("typ") != _OAUTH_NGINX_STATE_TYPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth nginx state type",
        )
    return claims


def _decode_oauth_access_token(access_token: str) -> dict:
    try:
        claims = jwt.decode(
            access_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=_OAUTH_AUDIENCE,
        )
    except JWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OAuth access token",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )
    if claims.get("typ") != _OAUTH_ACCESS_TYPE:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OAuth access token type",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )
    return claims


@router.get(
    "/registry/token",
    response_model=RegistryTokenResponse,
    summary="Docker Registry token endpoint",
    tags=["Интеграции: Docker Registry"],
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
        token_headers: dict[str, list[str]] | None = None
        if settings.REGISTRY_TOKEN_CERT_PATH:
            token_headers = {"x5c": _load_registry_x5c_chain()}
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    token = jwt.encode(
        claims,
        registry_key,
        algorithm=settings.REGISTRY_TOKEN_ALGORITHM,
        headers=token_headers,
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
    tags=["Интеграции: Service Auth"],
)
def whoami(current_user: User = Depends(get_current_user)):
    return _build_identity(current_user)


@router.get(
    "/basic/verify",
    response_model=IntegrationIdentity,
    summary="Verify credentials for reverse-proxy integrations",
    tags=["Интеграции: Service Auth"],
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
    tags=["Интеграции: Service Auth"],
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
    "/oauth/nginx/start",
    summary="Start OAuth flow for nginx auth_request integrations",
    tags=["Интеграции: OAuth"],
)
def oauth_nginx_start(
    client_id: str = Query(..., description="OAuth client id"),
    redirect_uri: str = Query(..., description="OAuth callback URL"),
    rd: str = Query(..., description="URL to return after successful auth"),
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    oauth_client = get_oauth_client_by_client_id(db, client_id.strip())
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Unknown OAuth client id",
        )

    if not _is_allowed_oauth_redirect_uri(oauth_client, redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not allowed",
        )

    if not _is_allowed_redirect_target(rd):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="rd is not allowed",
        )

    requested_scope = _normalize_oauth_scope(scope, oauth_client)
    state = _build_oauth_nginx_state(
        client_id=oauth_client.client_id,
        redirect_uri=redirect_uri,
        rd=rd,
        scope=requested_scope,
    )
    authorize_url = _append_query_params(
        "/api/auth/v1/integrations/oauth/authorize",
        response_type="code",
        client_id=oauth_client.client_id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        state=state,
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/oauth/nginx/callback",
    summary="Finish OAuth flow for nginx auth_request integrations",
    tags=["Интеграции: OAuth"],
)
def oauth_nginx_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    state_claims = _decode_oauth_nginx_state(state)
    client_id = str(state_claims.get("client_id") or "").strip()
    redirect_uri = str(state_claims.get("redirect_uri") or "").strip()
    rd = str(state_claims.get("rd") or "").strip()

    if not client_id or not redirect_uri or not rd:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="OAuth nginx state payload is invalid",
        )

    if not _is_allowed_redirect_target(rd):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="rd is not allowed",
        )

    oauth_client = get_oauth_client_by_client_id(db, client_id)
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="OAuth client is disabled",
        )

    if not _is_allowed_oauth_redirect_uri(oauth_client, redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not allowed",
        )

    code_claims = _decode_oauth_code(code)
    if str(code_claims.get("client_id") or "") != client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code client mismatch",
        )
    if str(code_claims.get("redirect_uri") or "") != redirect_uri:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code redirect URI mismatch",
        )

    raw_user_id = code_claims.get("user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code payload is invalid",
        )

    user = get_user(db, user_id)
    if not user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="User from authorization code no longer exists",
        )

    required_permission = (oauth_client.required_permission or "").strip()
    if required_permission and not user.has_permission(required_permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for OAuth client access",
        )

    access_token = _build_browser_access_token(user, db)
    response = RedirectResponse(url=rd, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    return response


@router.get(
    "/oauth/authorize",
    response_class=HTMLResponse,
    summary="OAuth2 authorize endpoint",
    tags=["Интеграции: OAuth"],
)
def oauth_authorize(
    response_type: str = Query(..., description="Must be 'code'"),
    client_id: str = Query(..., description="OAuth client id"),
    redirect_uri: str = Query(..., description="OAuth callback URL"),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    oauth_client = get_oauth_client_by_client_id(db, client_id.strip())
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Unknown OAuth client id",
        )

    if not _is_allowed_oauth_redirect_uri(oauth_client, redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not allowed",
        )

    if response_type != "code":
        return _oauth_redirect_response(
            redirect_uri,
            state=state,
            error="unsupported_response_type",
            error_description="response_type must be 'code'",
        )

    requested_scope = _normalize_oauth_scope(scope, oauth_client)
    return _render_oauth_login_form(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        state=state,
        service_name=oauth_client.display_name,
    )


@router.post(
    "/oauth/authorize",
    response_class=HTMLResponse,
    summary="OAuth2 authorize form submit",
    tags=["Интеграции: OAuth"],
)
def oauth_authorize_submit(
    response_type: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str | None = Form(default=None),
    state: str | None = Form(default=None),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    oauth_client = get_oauth_client_by_client_id(db, client_id.strip())
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Unknown OAuth client id",
        )

    if not _is_allowed_oauth_redirect_uri(oauth_client, redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not allowed",
        )

    if response_type != "code":
        return _oauth_redirect_response(
            redirect_uri,
            state=state,
            error="unsupported_response_type",
            error_description="response_type must be 'code'",
        )

    requested_scope = _normalize_oauth_scope(scope, oauth_client)

    user = get_user_by_login(db, username.strip())
    if not user or not verify_password(password, user.password_hash):
        return _render_oauth_login_form(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=requested_scope,
            state=state,
            service_name=oauth_client.display_name,
            username=username,
            error_message="Неверный логин или пароль",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    required_permission = (oauth_client.required_permission or "").strip()
    if required_permission and not user.has_permission(required_permission):
        return _render_oauth_login_form(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=requested_scope,
            state=state,
            service_name=oauth_client.display_name,
            username=username,
            error_message="Недостаточно прав для доступа к сервису",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    code = _build_oauth_code(
        user,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
    )
    return _oauth_redirect_response(redirect_uri, state=state, code=code)


@router.post(
    "/oauth/token",
    response_model=OAuthTokenResponse,
    summary="OAuth2 token endpoint",
    tags=["Интеграции: OAuth"],
)
def oauth_access_token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if grant_type != "authorization_code":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type",
        )

    resolved_client_id, resolved_client_secret = _resolve_oauth_client_credentials(
        request,
        client_id=client_id,
        client_secret=client_secret,
    )
    oauth_client = _validate_oauth_client_credentials(
        db,
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
    )

    if not _is_allowed_oauth_redirect_uri(oauth_client, redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not allowed",
        )

    code_claims = _decode_oauth_code(code)
    if code_claims.get("client_id") != resolved_client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code client mismatch",
        )
    if code_claims.get("redirect_uri") != redirect_uri:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code redirect URI mismatch",
        )

    raw_user_id = code_claims.get("user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Authorization code payload is invalid",
        )

    user = get_user(db, user_id)
    if not user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="User from authorization code no longer exists",
        )

    required_permission = (oauth_client.required_permission or "").strip()
    if required_permission and not user.has_permission(required_permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for OAuth client access",
        )

    requested_scope = str(code_claims.get("scope") or oauth_client.default_scope)
    return _build_oauth_token(
        user,
        client_id=resolved_client_id,
        scope=requested_scope,
    )


@router.get(
    "/oauth/userinfo",
    response_model=OAuthUserInfoResponse,
    summary="OAuth2 userinfo endpoint",
    tags=["Интеграции: OAuth"],
)
def oauth_userinfo(
    request: Request,
    db: Session = Depends(get_db),
):
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )

    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )

    access_claims = _decode_oauth_access_token(credentials)
    raw_user_id = access_claims.get("user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="OAuth access token payload is invalid",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )

    user = get_user(db, user_id)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="User from OAuth access token no longer exists",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )

    oauth_client_id = str(access_claims.get("client_id") or "").strip()
    oauth_client = get_oauth_client_by_client_id(db, oauth_client_id)
    if not oauth_client or not oauth_client.enabled:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="OAuth client is disabled",
            headers=_OAUTH_ACCESS_CHALLENGE,
        )

    required_permission = (oauth_client.required_permission or "").strip()
    if required_permission and not user.has_permission(required_permission):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for OAuth client access",
        )
    return _build_oauth_profile(user)
