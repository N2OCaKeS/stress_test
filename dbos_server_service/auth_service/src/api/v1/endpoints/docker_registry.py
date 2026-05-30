"""Эндпоинты Docker Registry auth + per-department конфиг.

Docker registry config.yml:
  auth:
    token:
      realm: https://<host>/api/auth/v1/docker/token
      service: registry.example.com
      issuer: auth_service
"""

import base64

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.docker_jwt import get_jwks, get_public_key_pem
from src.core.exceptions import AuthenticationError
from src.dependencies.auth import AnyAdmin
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.docker_registry import (
    DockerRegistryConfigCreate,
    DockerRegistryConfigResponse,
    DockerRegistryConfigUpdate,
    DockerTokenResponse,
)
from src.services import docker_registry_service

router = APIRouter(prefix="/docker")


def _parse_basic_auth(authorization: str) -> tuple[str, str]:
    """Распарсить Basic Authorization header.

    Caller обязан проверить, что header вообще пришёл — None/пустую строку
    сюда передавать нельзя (для анонимного flow `docker_token` отдельно
    выбирает ветку `anonymous=True`). Non-Basic header (например `Bearer …`)
    → MISSING_CREDENTIALS.
    """
    if not authorization.lower().startswith("basic "):
        raise AuthenticationError(
            error_code="MISSING_CREDENTIALS",
            message="Basic authentication required",
        )
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
        return username, password
    except Exception:
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Malformed Basic auth header")


def _scope_has_push(scope: str) -> bool:
    """Грубая проверка: пушит ли клиент. Чистый pull-only scope разрешает anon."""
    for part in scope.split():
        segments = part.split(":")
        if len(segments) >= 3 and "push" in segments[-1].split(","):
            return True
    return False


def _scope_has_pull(scope: str) -> bool:
    for part in scope.split():
        segments = part.split(":")
        if len(segments) >= 3 and "pull" in segments[-1].split(","):
            return True
    return False


# ── Управление конфигом (dept_admin или account_admin) ───────────────────────

@router.put(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Создать / заменить Docker registry конфиг отдела",
    description="PUT — replace-семантика. Перезаписывает весь конфиг отдела.",
)
async def create_or_replace_registry_config(
    department_id: str,
    body: DockerRegistryConfigCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    """Включить или перезаписать Docker registry для отдела.

    Что делает:
        Создаёт или заменяет `DepartmentDockerRegistry`. После этого юзеры
        отдела смогут получать scoped JWT через `/docker/token`.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await docker_registry_service.create_or_replace_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Обновить Docker registry конфиг отдела",
    description="PATCH — частичный update. Передавай только меняющиеся поля.",
)
async def update_registry_config(
    department_id: str,
    body: DockerRegistryConfigUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    """Patch Docker registry конфига.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await docker_registry_service.update_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/registry/{department_id}",
    response_model=DockerRegistryConfigResponse,
    summary="Получить Docker registry конфиг отдела",
)
async def get_registry_config(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> DockerRegistryConfigResponse:
    """Текущий конфиг registry для отдела.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await docker_registry_service.get_config(
        db=db,
        department_id=department_id,
        actor_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/registry/{department_id}",
    response_model=OkResponse,
    summary="Отключить Docker registry для отдела",
    description="Удаляет запись `DepartmentDockerRegistry`. Юзеры отдела перестанут получать scoped JWT.",
)
async def disable_registry(
    department_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Снести Docker registry конфиг отдела."""
    await docker_registry_service.delete_config(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


# ── Token endpoint (Docker protocol) ─────────────────────────────────────────

@router.get(
    "/token",
    response_model=DockerTokenResponse,
    summary="Docker registry token auth (Basic credentials → scoped JWT)",
    description="Реализация Docker token auth protocol. Возвращает RS256-подписанный JWT с access-claims для запрошенного scope.",
)
async def docker_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: str = Query(default=""),
    scope: str = Query(default=""),
    account: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> DockerTokenResponse:
    """Выдать Docker JWT по Basic credentials.

    Что делает:
        Парсит Basic auth (`username:password`), валидирует пароль через
        тот же lockout-pipeline что и `/login` (5 неудачных → 15 мин
        lockout, 429 + `retry_after_seconds`). При успехе строит RS256 JWT
        со scope'ами `repository:<name>:pull/push`. Подпись — `DOCKER_RSA_PRIVATE_KEY`.

    Варианты auth (через Basic):
        * `username:password` — обычные кредлы юзера;
        * `username:dbos_pat_…` — PAT как пароль;
        * `botname:dbos_bot_…` — bot-токен как пароль.

    Анонимный pull:
        Если `Authorization` не прислан и scope состоит только из
        `pull`-action'ов — issuer вернёт JWT с `access`, ограниченным
        registry с `pull_policy='all'`. Пустой scope без header → 401
        (нечего выдавать, и legacy-тесты на пустой scope этого ждут).

    Возможные ошибки:
        * `MISSING_CREDENTIALS` / `INVALID_CREDENTIALS` (401).
        * `ACCOUNT_TEMPORARILY_LOCKED` (429) — lockout после 5 неудач.
        * `PUSH_DEPT_MISMATCH` (403) — push в registry чужого отдела.
    """
    if not authorization:
        # Анонимный путь — только если scope чисто pull (и не пустой).
        if scope and _scope_has_pull(scope) and not _scope_has_push(scope):
            return await docker_registry_service.issue_token(
                db=db,
                username="",
                password="",
                service=service,
                scope=scope,
                request_id=getattr(request.state, "request_id", None),
                anonymous=True,
            )
        raise AuthenticationError(
            error_code="MISSING_CREDENTIALS",
            message="Basic authentication required",
        )
    username, password = _parse_basic_auth(authorization)
    return await docker_registry_service.issue_token(
        db=db,
        username=username,
        password=password,
        service=service,
        scope=scope,
        request_id=getattr(request.state, "request_id", None),
    )


# ── Public-key endpoints (для registry rootcertbundle) ──────────────────────

@router.get(
    "/certs",
    response_class=PlainTextResponse,
    summary="RSA public key (PEM) — для Docker registry rootcertbundle",
    tags=["docker-registry"],
)
async def docker_public_key() -> str:
    """RSA public key для верификации Docker JWT.

    Сохрани в файл и пропиши в registry config:
      auth.token.rootcertbundle: /path/to/this.pem
    """
    return get_public_key_pem()


@router.get(
    "/jwks",
    summary="JWKS endpoint — public keys для JWT verification",
    tags=["docker-registry"],
)
async def docker_jwks() -> dict:
    """JSON Web Key Set — стандартный формат для JWT-aware инструментов."""
    return get_jwks()
