"""Хелперы для тестов кластера I (OAuth2 + introspect).

Кладём сюда всё, что переиспользуется несколькими `test_e2e_I_*.py`:

* PKCE: code_verifier + code_challenge (RFC 7636 §4.1/4.2);
* идемпотентное создание department/service/grant — каталог в integration
  stack общий, удалять/пересоздавать его между тестами нельзя;
* setup OAuth2-клиента + выписка authorization-кода для authorization_code
  flow;
* обращение к `POST /authorization/introspect` под shared `SERVICE_API_KEY`
  (значение зашито в `docker-compose.test.yml`);
* ожидание audit-события в loging_service после действия.

В сервисный код не лезем, conftest не трогаем — используем существующие
фикстуры (`auth_client`, `logging_client`, `make_user`, `make_bot`, ...).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid

import httpx

# ── Константы и URL'ы ────────────────────────────────────────────────────────

AUTH_BASE = "/api/auth/v1"
INTROSPECT_URL = f"{AUTH_BASE}/authorization/introspect"
SERVICE_ACCESS_URL = f"{AUTH_BASE}/authorization/service-access"
OAUTH_CLIENTS_URL = f"{AUTH_BASE}/oauth2/clients"
OAUTH_TOKEN_URL = f"{AUTH_BASE}/oauth2/token"
OAUTH_AUTHORIZE_URL = f"{AUTH_BASE}/oauth2/authorize"
TOKEN_FORM_URL = f"{AUTH_BASE}/token"
LOGIN_JSON_URL = f"{AUTH_BASE}/login"
DEPARTMENTS_URL = f"{AUTH_BASE}/departments"
SERVICES_URL = f"{AUTH_BASE}/services"
USERS_URL = f"{AUTH_BASE}/users"
BOTS_URL = f"{AUTH_BASE}/bots"

# Зашитый в docker-compose.test.yml shared key для service-to-service путей.
# В compose у auth-service `SERVICE_API_KEY=test-logging-api-key`; этой же
# строкой закрыты `/authorization/introspect` и `/authorization/service-access`.
SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "test-logging-api-key")


def short_id() -> str:
    """8 hex-символов — для уникальных имён dept/service/client."""
    return uuid.uuid4().hex[:8]


# ── PKCE (RFC 7636) ──────────────────────────────────────────────────────────

def make_pkce_pair() -> tuple[str, str]:
    """Сгенерить (code_verifier, code_challenge) для S256.

    RFC 7636 §4.1: verifier — высокоэнтропийная случайная строка длиной 43-128
    символов из `[A-Z][a-z][0-9]-._~`; генерим из 32 случайных байт →
    base64url без padding (43 символа).

    RFC 7636 §4.2: challenge = BASE64URL(SHA256(verifier)) без `=`-padding.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Каталог: dept / service / grant ──────────────────────────────────────────

def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Идемпотентно создать отдел; вернуть его id."""
    r = auth_client.post(
        DEPARTMENTS_URL,
        headers=_admin_headers(admin_token),
        json={"name": name, "display_name": name.title()},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    listing = auth_client.get(DEPARTMENTS_URL, headers=_admin_headers(admin_token)).json()
    return next(d["department_id"] for d in listing if d["name"] == name)


def ensure_service(auth_client: httpx.Client, admin_token: str, name: str) -> None:
    """Идемпотентно создать платформенный сервис."""
    auth_client.post(
        SERVICES_URL,
        headers=_admin_headers(admin_token),
        json={"service_name": name, "display_name": name.title()},
    )


def grant_service_to_department(
    auth_client: httpx.Client, admin_token: str, dept_id: str, service_name: str
) -> None:
    """Дать отделу доступ к сервису (idempotent — 409 значит уже есть)."""
    auth_client.post(
        f"{DEPARTMENTS_URL}/{dept_id}/services",
        headers=_admin_headers(admin_token),
        json={"service_name": service_name},
    )


def ensure_service_role(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    service_name: str,
    role_name: str,
) -> None:
    """Идемпотентно создать `ServiceRoleDefinition` в scope (dept, service, role).

    Платформа авто-сеет только `admin` (`is_system=True`). Остальные «системные»
    имена (`reader`, `operator`, `guest`) — обычные кастомные роли, их надо
    зарегистрировать перед тем, как назначать юзеру/боту.
    """
    if role_name == "admin":
        return  # уже засеяна автоматически при grant_service_to_department
    r = auth_client.post(
        f"{DEPARTMENTS_URL}/{department_id}/services/{service_name}/roles",
        headers=_admin_headers(admin_token),
        json={"role_name": role_name, "display_name": role_name.title()},
    )
    # 201 — создали; 409 — уже была.
    assert r.status_code in (201, 409), (
        f"ensure_service_role failed: {r.status_code} {r.text}"
    )


# ── OAuth2 client / authorization code ──────────────────────────────────────

def create_oauth_client(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    name: str | None = None,
    redirect_uris: list[str] | None = None,
    allowed_scopes: list[str] | None = None,
    grant_types: list[str] | None = None,
) -> dict:
    """Создать OAuth2-клиента; возвращает body со `client_secret` (показан один раз)."""
    body = {
        "name": name or f"client_{short_id()}",
        "department_id": department_id,
        "redirect_uris": redirect_uris or ["https://app.example.com/cb"],
        "allowed_scopes": allowed_scopes or [],
        "grant_types": grant_types or ["authorization_code"],
    }
    r = auth_client.post(OAUTH_CLIENTS_URL, headers=_admin_headers(admin_token), json=body)
    assert r.status_code == 201, f"create_oauth_client failed: {r.status_code} {r.text}"
    return r.json()


def request_authorization_code(
    auth_client: httpx.Client,
    *,
    user_access_token: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "",
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
) -> tuple[str, str | None]:
    """Сходить на `/oauth2/authorize` под юзерским JWT, вернуть (code, state).

    `auth_client` — httpx.Client без follow_redirects — нам нужно достать
    `Location: <redirect_uri>?code=…&state=…` из 302-ответа.
    """
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
    }
    if state is not None:
        params["state"] = state
    if code_challenge:
        params["code_challenge"] = code_challenge
    if code_challenge_method:
        params["code_challenge_method"] = code_challenge_method

    r = auth_client.get(
        OAUTH_AUTHORIZE_URL,
        params=params,
        headers=_admin_headers(user_access_token),
        follow_redirects=False,
    )
    assert r.status_code == 302, f"authorize did not redirect: {r.status_code} {r.text}"

    location = r.headers["Location"]
    # `https://app.example.com/cb?code=…&state=…`
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    code = qs["code"][0]
    state_back = qs.get("state", [None])[0]
    return code, state_back


def exchange_code(
    auth_client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> httpx.Response:
    """POST /oauth2/token с `grant_type=authorization_code`."""
    body = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier is not None:
        body["code_verifier"] = code_verifier
    return auth_client.post(OAUTH_TOKEN_URL, json=body)


def client_credentials_token(
    auth_client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
) -> httpx.Response:
    """POST /oauth2/token с `grant_type=client_credentials`."""
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return auth_client.post(OAUTH_TOKEN_URL, json=body)


# ── Introspect ───────────────────────────────────────────────────────────────

def introspect_token(auth_client: httpx.Client, token: str) -> httpx.Response:
    """POST /authorization/introspect под shared SERVICE_API_KEY."""
    return auth_client.post(
        INTROSPECT_URL,
        json={"token": token},
        headers={
            "Authorization": f"Bearer {SERVICE_API_KEY}",
            "X-Service-Identity": "loging_service",
        },
    )


# ── JWT-payload без верификации подписи ──────────────────────────────────────

def jwt_unverified_payload(token: str) -> dict:
    """Достать payload из JWT без верификации подписи — для assert'ов в тестах.

    Прод-код всегда валидирует подпись через `decode_access_token`; в тестах
    нам нужно только убедиться, что `sub`/`actor_type`/etc лежат там, где
    обещано.
    """
    import json
    parts = token.split(".")
    assert len(parts) == 3, f"not a JWT: {token!r}"
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))


# ── Identity bootstrap для тестов ────────────────────────────────────────────

def make_user_in_dept(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    services_with_roles: dict[str, list[str]] | None = None,
    password: str = "UserPass1!",
    username: str | None = None,
) -> dict:
    """Создать юзера в отделе. Если `services_with_roles` задан — назначить роли.

    Возвращает body create-ответа + `_password`.
    """
    body = {
        "username": username or f"u_{short_id()}",
        "password": password,
        "department_id": department_id,
    }
    r = auth_client.post(USERS_URL, headers=_admin_headers(admin_token), json=body)
    assert r.status_code in (200, 201), f"make_user_in_dept failed: {r.status_code} {r.text}"
    user = r.json()
    user["_password"] = password
    user.setdefault("username", body["username"])
    # `UserResponse` отдаёт `user_id`; в тестах исторически обращаемся через
    # `user["id"]` — добавим алиас, чтобы не размазывать `.get("user_id") or .get("id")`
    # по всем кластерным тестам.
    if "id" not in user and "user_id" in user:
        user["id"] = user["user_id"]
    if services_with_roles:
        for svc, roles in services_with_roles.items():
            assign_service_roles(
                auth_client, admin_token, user["id"], svc, roles,
                department_id=department_id,
            )
    return user


def assign_service_roles(
    auth_client: httpx.Client,
    admin_token: str,
    user_id: str,
    service_name: str,
    roles: list[str],
    *,
    department_id: str | None = None,
) -> None:
    """Назначить юзеру service-роли (через bulk_assign endpoint).

    Если `department_id` задан — перед назначением идемпотентно регистрируем
    каждую роль в `ServiceRoleDefinition`. Платформа авто-сеет только `admin`;
    `reader`/`operator`/`guest` требуют явного create.
    """
    if department_id is not None:
        for role in roles:
            ensure_service_role(
                auth_client, admin_token,
                department_id=department_id, service_name=service_name, role_name=role,
            )
    r = auth_client.post(
        f"{USERS_URL}/{user_id}/roles",
        headers=_admin_headers(admin_token),
        json={"service_name": service_name, "roles": roles},
    )
    assert r.status_code in (200, 201), f"assign_service_roles failed: {r.status_code} {r.text}"


def login_user(auth_client: httpx.Client, username: str, password: str) -> dict:
    """`POST /login` — вернуть JSON-ответ (access_token + refresh)."""
    r = auth_client.post(LOGIN_JSON_URL, json={"username": username, "password": password})
    return {"status_code": r.status_code, "body": (r.json() if r.content else None)}


def ban_user(auth_client: httpx.Client, admin_token: str, user_id: str) -> None:
    """Permanent-ban через `/users/{id}/ban`."""
    r = auth_client.post(
        f"{USERS_URL}/{user_id}/ban",
        headers=_admin_headers(admin_token),
        json={"ban_type": "permanent", "reason": "test"},
    )
    assert r.status_code == 200, f"ban_user failed: {r.status_code} {r.text}"


# ── Опрос введёных событий с retry (нет статичного `wait_for`, как в conftest) ─

def issue_pat(
    auth_client: httpx.Client,
    user_access_token: str,
    *,
    name: str | None = None,
    allowed_services: list[str] | None = None,
    expires_at: str | None = None,
) -> dict:
    """Выписать PAT под юзерским JWT — возвращает body со `token` plaintext.

    Используем вместо conftest-фикстуры `pat_token`, потому что та принимает
    `scope`, а схема PATCreate ждёт `allowed_services` (extra-поля Pydantic
    v2 по умолчанию игнорирует, и PAT уходит с пустым allowed_services →
    introspect отдаёт пустой effective_services).
    """
    body: dict = {
        "name": name or f"pat_{short_id()}",
        "allowed_services": allowed_services if allowed_services is not None else ["auth_service"],
    }
    if expires_at is not None:
        body["expires_at"] = expires_at
    r = auth_client.post(
        f"{AUTH_BASE}/tokens",
        headers={"Authorization": f"Bearer {user_access_token}"},
        json=body,
    )
    assert r.status_code in (200, 201), f"issue_pat failed: {r.status_code} {r.text}"
    return r.json()


def wait_until(predicate, *, retries: int = 20, delay: float = 0.2):
    """Простой busy-wait для проверки eventual-consistent условий."""
    last = None
    for _ in range(retries):
        try:
            result = predicate()
            if result:
                return result
            last = result
        except AssertionError as exc:
            last = exc
        time.sleep(delay)
    if isinstance(last, BaseException):
        raise last
    raise AssertionError("wait_until: predicate never became truthy")
