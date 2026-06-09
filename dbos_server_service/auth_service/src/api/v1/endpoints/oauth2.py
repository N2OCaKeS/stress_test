"""Эндпоинты OAuth2: client management и authorize/token flow."""

from urllib.parse import quote, urlparse, urlunparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import DomainValidationError
from src.dependencies.auth import AnyAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreatedResponse,
    OAuthClientResponse,
    OAuthTokenRequest,
    OAuthTokenResponse,
)
from src.services import oauth_service

router = APIRouter(prefix="/oauth2")

# RFC 6749 §3.1.1 + §4.1.2.1: только `code` flow (authorization code grant).
# Implicit (`token`) и hybrid (`id_token`/`code id_token`) flow не реализованы
# и СОЗНАТЕЛЬНО отказаны: implicit отдаёт access_token прямо в URL fragment
# (history-leak / referer-leak), id_token подразумевает OIDC, который мы не
# делаем. Любой неизвестный response_type → 400 `unsupported_response_type`
# (RFC 6749 §4.1.2.1).
_SUPPORTED_RESPONSE_TYPES: frozenset[str] = frozenset({"code"})


def _extract_raw_state(raw_query: str) -> str | None:
    """Достать сырое значение `state` из query-string запроса.

    Возвращаем именно raw-байты (последовательность символов между `state=` и
    следующим `&` либо концом строки), без `urldecode`. Если клиент прислал
    `state=abc%20def`, возвращаем `"abc%20def"` — эту строку echo'им в
    redirect 1:1, и при декоде клиентом получится тот же `"abc def"`, что и
    у FastAPI Query-параметра. Если параметр повторяется — берём первое
    вхождение (стандартное поведение Query). None если ключа нет.

    Не используем `parse_qsl`: он бы декодил значение, обнуляя смысл всего
    упражнения. Парсим вручную линейно — query короткая, regex не нужен.
    """
    if not raw_query:
        return None
    for chunk in raw_query.split("&"):
        if chunk == "state" or chunk.startswith("state="):
            if "=" not in chunk:
                return ""
            return chunk.split("=", 1)[1]
    return None


# ── Управление клиентами ─────────────────────────────────────────────────────

@router.post(
    "/clients",
    response_model=OAuthClientCreatedResponse,
    status_code=201,
    summary="Создать OAuth2-клиента",
    description="Возвращает client_secret один раз. В БД лежит хэш.",
)
async def create_client(
    body: OAuthClientCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OAuthClientCreatedResponse:
    """Регистрация OAuth2 клиента.

    Что делает:
        Создаёт `OAuthClient` + генерит client_secret. Plaintext secret
        возвращается ровно один раз. Все redirect_uris должны быть https
        (или http://localhost для нативных клиентов).

    Доступ:
        account_admin (любой отдел) или department_admin (только свой).
    """
    return await oauth_service.create_client(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/clients",
    response_model=list[OAuthClientResponse],
    summary="Список OAuth2-клиентов",
    description="Опциональный фильтр по `department_id`.",
)
async def list_clients(
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
    department_id: str | None = Query(default=None),
) -> list[OAuthClientResponse]:
    """Список клиентов с учётом scope-а смотрящего."""
    return await oauth_service.list_clients(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/clients/{client_id}",
    response_model=OkResponse,
    summary="Удалить OAuth2-клиента",
    description=(
        "Soft-delete клиента (`is_active=False`). Выпущенные authorization-codes "
        "и access-токены не удаляются сразу — они истекают по TTL и отбиваются "
        "на introspect через `is_active`-check."
    ),
)
async def delete_client(
    client_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снести OAuth2-клиента."""
    await oauth_service.delete_client(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        client_db_id=client_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


# ── Authorization code flow ───────────────────────────────────────────────────

@router.get(
    "/authorize",
    summary="OAuth2 authorize endpoint (authorization code flow)",
    description="Выдаёт authorization code и редиректит на redirect_uri. PKCE опционален для confidential, обязателен для public client'ов.",
    responses={
        302: {"description": "Redirect на `redirect_uri?code=…&state=…`."},
        400: {"description": "`UNSUPPORTED_RESPONSE_TYPE` — response_type не `code`."},
        401: {"description": "`OAUTH_CLIENT_INVALID` — нет client_id или клиент деактивирован."},
        403: {"description": "`REDIRECT_URI_MISMATCH` / `GRANT_TYPE_NOT_ALLOWED` / `PKCE_REQUIRED` / `PKCE_METHOD_INVALID`."},
    },
)
async def authorize(
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(default=""),
    state: str | None = Query(default=None, max_length=2048),
    response_type: str = Query(default="code"),
    code_challenge: str | None = Query(default=None),
    code_challenge_method: str | None = Query(default=None),
):
    """Выдать authorization code и редиректнуть на redirect_uri.

    Что делает:
        Создаёт `OAuthAuthorizationCode` (TTL = `OAUTH_CODE_TTL_SECONDS`,
        default 300), редиректит 302 на `redirect_uri?code=…&state=…`.
        Поддерживает PKCE (RFC 7636) через `code_challenge` + метод.

    Доступ:
        Любой залогиненный юзер. m2m-JWT (`actor_type=oauth_client`)
        отбивается `require_user_context` guard'ом.

    PKCE (RFC 7636):
        `code_challenge` + `code_challenge_method` опциональны — для public
        client'ов (SPA/CLI) требуются обязательно, для confidential client'ов
        с client_secret допустимо legacy-поведение.

    response_type:
        Валидируется по `_SUPPORTED_RESPONSE_TYPES`: ровно `code`. Implicit
        (`token`) и hybrid (`id_token`, `code id_token`) flow запрещены — они
        либо отдают access_token через URL fragment (history-leak /
        referer-leak), либо требуют OIDC, который мы не реализуем.

    Возможные ошибки:
        * `UNSUPPORTED_RESPONSE_TYPE` (400) — не `code`.
        * `REDIRECT_URI_MISMATCH` (403) — uri не в whitelist'е клиента.
        * `OAUTH_CLIENT_INVALID` (401) — нет такого client_id или клиент деактивирован.
        * `GRANT_TYPE_NOT_ALLOWED` (403) — `authorization_code` не в `grant_types` клиента.
        * `PKCE_REQUIRED` / `PKCE_METHOD_INVALID` (403).
    """
    if response_type not in _SUPPORTED_RESPONSE_TYPES:
        raise DomainValidationError(
            http_status=400,
            error_code="UNSUPPORTED_RESPONSE_TYPE",
            message=(
                f"response_type '{response_type}' is not supported; "
                f"only 'code' is allowed"
            ),
        )
    scopes = scope.split() if scope else []
    code = await oauth_service.issue_authorization_code(
        db=db,
        client_id=client_id,
        user_id=identity.user_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        request_id=getattr(request.state, "request_id", None),
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    # `redirect_uri` уже валидирован whitelist'ом клиента в issue_authorization_code,
    # но в whitelist может лежать URI с собственными query-параметрами
    # (`https://app/cb?env=prod`). Тупой `f"{redirect_uri}?code=…"` тогда даёт
    # `…?env=prod?code=…` — второй `?` сламывает парсинг на клиенте. Разбираем
    # URI и дописываем `code`/`state` к существующему query без переэкодирования.
    #
    # `state` echo'им из СЫРОЙ query запроса (`request.url.query`), а не из
    # FastAPI-параметра `state`: Query-параметр приходит уже URL-decoded
    # (`state=abc%20def` → `"abc def"`), и любой re-encode даёт расхождение
    # байт-в-байт с тем, что прислал клиент. Раньше использовался
    # `quote(state, safe='')`, который для уже-encoded литералов вроде
    # `state=abc%20def` выдавал `abc%2520def` (двойной encode) — некоторые
    # клиенты (Auth0/Keycloak SDK) сравнивают callback'овый `state` как opaque
    # байты со своим хранилищем и ломались на `%25`-разнице. RFC 6749 §4.1.2
    # требует exact echo: возвращаем то же значение, что клиент прислал
    # в query, без второго encode/decode-раунда.
    parsed = urlparse(redirect_uri)
    appended = f"code={quote(code, safe='')}"
    if state:
        raw_state = _extract_raw_state(request.url.query)
        # Fallback на encode-через-quote только если raw-извлечение почему-то
        # не нашло параметр (теоретически невозможно — Query прокинул значение
        # ⇒ ключ есть в URL'е).
        echo_state = raw_state if raw_state is not None else quote(state, safe="")
        appended += f"&state={echo_state}"
    new_query = f"{parsed.query}&{appended}" if parsed.query else appended
    location = urlunparse(parsed._replace(query=new_query))
    return RedirectResponse(url=location, status_code=302)


@router.post(
    "/token",
    response_model=OAuthTokenResponse,
    summary="OAuth2 token endpoint",
    description="Обмен authorization code → access_token, либо client_credentials grant.",
)
async def token(
    body: OAuthTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OAuthTokenResponse:
    """Получить access_token.

    Что делает:
        Поддерживает `authorization_code` (с PKCE) и `client_credentials`
        (m2m, без user_id). Authorization code разовый (`mark_used` через
        CAS), reuse отбивается с убийством issued-токена.

    Возможные ошибки:
        * `INVALID_GRANT` — code не найден/истёк/уже использован.
        * `OAUTH_CLIENT_INVALID` (401) — неверный client_id/secret или клиент деактивирован.
        * `UNSUPPORTED_GRANT_TYPE` — не `authorization_code` и не `client_credentials`.
    """
    request_id = getattr(request.state, "request_id", None)
    if body.grant_type == "authorization_code":
        return await oauth_service.exchange_code(
            db=db,
            client_id=body.client_id or "",
            client_secret=body.client_secret or "",
            code=body.code or "",
            redirect_uri=body.redirect_uri or "",
            request_id=request_id,
            code_verifier=body.code_verifier,
        )
    if body.grant_type == "client_credentials":
        return await oauth_service.client_credentials_token(
            db=db,
            client_id=body.client_id or "",
            client_secret=body.client_secret or "",
            request_id=request_id,
        )
    raise DomainValidationError(
        error_code="UNSUPPORTED_GRANT_TYPE",
        message=f"grant_type '{body.grant_type}' is not supported",
    )
