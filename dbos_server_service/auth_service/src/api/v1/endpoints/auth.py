"""Эндпоинты логина, refresh, logout и identity-контекста."""

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.auth import IdentityContext, LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, RefreshResponse
from src.schemas.common import OkResponse
from src.services import auth_service

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness-проба",
    description="Простой liveness-чек. Не ходит в БД — нужен только чтобы понять, что процесс жив.",
)
async def healthcheck() -> dict[str, str]:
    """Liveness — отдаёт `{"status": "ok"}` всегда, если процесс отвечает."""
    return {"status": "ok", "service": "auth_service"}


@router.get(
    "/ready",
    summary="Readiness-проба",
    description="Readiness-чек. Используется Kubernetes для определения готовности принимать трафик.",
)
async def readiness() -> dict[str, str]:
    """Readiness — отдаёт `{"status": "ready"}` если сервис готов принимать запросы."""
    return {"status": "ready", "service": "auth_service"}


@router.post(
    "/token",
    response_model=LoginResponse,
    summary="OAuth2 Password flow (для Swagger UI Authorize)",
    include_in_schema=False,
)
async def token_form(
    form: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """OAuth2 Password Grant — нужен только Swagger UI для кнопки Authorize.

    Скрыт из публичной схемы (`include_in_schema=False`), снаружи использовать
    `/login`. Внутри — тот же `auth_service.login`, разница только в формате
    тела (form-urlencoded вместо JSON).
    """
    return await auth_service.login(
        db=db,
        username=form.username,
        password=form.password,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("User-Agent") if request else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Логин по username/password",
    description="Аутентификация пользователя и выдача пары access + refresh токенов.",
    response_description="Пара токенов и контекст identity.",
    responses={
        401: {"description": "Неверный username/password или юзер забанен."},
        429: {"description": "Аккаунт залочен после 5 неудачных попыток (lockout 15 минут)."},
    },
)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Логин по паре username/password.

    Что делает:
        Проверяет пароль через Argon2id, выдаёт пару access JWT + refresh
        (opaque). На 5 неудачных подряд лочит аккаунт на 15 минут.

    Доступ:
        Публичный. Любой неаутентифицированный клиент.

    Возможные ошибки:
        * `INVALID_CREDENTIALS` (401) — неверный пароль или нет такого юзера.
        * `USER_BANNED` (401) — юзер забанен permanent/temporary.
        * `ACCOUNT_TEMPORARILY_LOCKED` (429) — слишком много неудачных попыток, надо ждать.

    Связано:
        * `POST /refresh` — обновить access по refresh.
        * `POST /logout` — отозвать refresh.
        * `GET /me` — получить identity по access.
    """
    return await auth_service.login(
        db=db,
        username=body.username,
        password=body.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Обновить access по refresh",
    description="Меняет refresh на новую пару. Старый refresh инвалидируется атомарно.",
    response_description="Новая пара access + refresh.",
    responses={
        401: {"description": "Refresh невалидный, истёк или уже использован."},
    },
)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    """Обновить access через refresh.

    Что делает:
        Ротация refresh: старый refresh инвалидируется через CAS, выдаётся
        новая пара access + refresh. Если refresh использован повторно
        (reuse-detection через `previous_token_hash`) — сессия убивается
        целиком, юзер вынужден логиниться заново.

    Доступ:
        Публичный (refresh — opaque secret, не Bearer).

    Возможные ошибки:
        * `REFRESH_TOKEN_INVALID` (401) — токен не найден, отозван, или
          обнаружена попытка повторного использования → kill-switch на всю
          сессию (audit-событие `token.refresh_reuse`).
        * `REFRESH_TOKEN_EXPIRED` (401) — refresh истёк.
        * `REFRESH_TOKEN_RACE` (401) — параллельный /refresh уже ротировал
          сессию (CAS-miss). Benign-race, повтор с новым refresh решает.
    """
    return await auth_service.refresh(
        db=db,
        raw_refresh_token=body.refresh_token,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/logout",
    response_model=OkResponse,
    summary="Logout — отозвать refresh",
    description="Инвалидирует переданный refresh. Access живёт до истечения TTL — короткий, ~10 мин.",
)
async def logout(
    body: LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Logout — отозвать refresh.

    Что делает:
        Помечает refresh как revoked в БД. Access не отзывается явно — он
        короткоживущий (10 мин), просто истечёт сам.

    Доступ:
        Публичный. Параметр — сам refresh, для несуществующего тихо ok.
    """
    await auth_service.logout(
        db=db,
        raw_refresh_token=body.refresh_token,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.get(
    "/me",
    response_model=IdentityContext,
    summary="Текущий identity-контекст",
    description="Возвращает свежий снимок identity: dept, platform_role, allowed_services, service_roles.",
    response_description="Identity-контекст текущего юзера/бота.",
    responses={
        401: {"description": "Нет Bearer-токена или токен невалидный."},
    },
)
async def me(request: Request, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db)) -> IdentityContext:
    """Получить свой identity-контекст.

    Что делает:
        Берёт `sub` из JWT и перечитывает свежие данные из БД — не из JWT
        payload. Это важно: чувствительные claims (`is_banned`,
        `service_roles`) в JWT не лежат, чтобы забаненный юзер не прошёл
        даже с валидной подписью.

    Доступ:
        Любой залогиненный юзер (Bearer). Бот/PAT тоже работают.
    """
    return await auth_service.get_identity(
        db=db,
        user_id=identity.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
