"""Эндпоинты логина, refresh, logout и identity-контекста."""

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import DomainValidationError
from src.db.session import AsyncSessionLocal
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.auth import IdentityContext, LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, RefreshResponse
from src.schemas.common import HealthResponse, OkResponse, ReadyResponse
from src.schemas.users import MeUpdateRequest
from src.services import auth_service, user_service
from src.services.audit_context import extract_client_ip

router = APIRouter()

# Имя cookie с refresh-токеном. Path сужен до auth-эндпоинтов: на остальные
# ручки cookie не уезжает, лишний трафик не палит токен в proxy/cdn-логах.
REFRESH_COOKIE_NAME = "dbos_refresh"
REFRESH_COOKIE_PATH = "/api/auth/v1"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Поставить HttpOnly Secure cookie с refresh-токеном.

    `secure` выключаем только в `app_env=local` — vite dev-сервер шлёт
    запросы по http://localhost, иначе браузер просто отбросит cookie.
    На прод/test/development `secure=True` обязателен.
    """
    settings = get_settings()
    ttl_seconds = settings.refresh_token_ttl_days * 24 * 60 * 60
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.app_env != "local",
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
        max_age=ttl_seconds,
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Снять refresh cookie. Path должен совпадать с тем, под которым ставился."""
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)

_readiness_logger = logging.getLogger(__name__)

# Внешний потолок на весь readiness-пинг. k8s readinessProbe.timeoutSeconds
# обычно 1s — стараемся ответить раньше, даже если pgsql тупит на checkout'е
# из пула. asyncio.wait_for режет вместе с TCP-таймером.
_READINESS_TIMEOUT_SECONDS = 0.5


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness-проба",
    description="Простой liveness-чек. Не ходит в БД — нужен только чтобы понять, что процесс жив.",
)
async def healthcheck() -> HealthResponse:
    """Liveness — отдаёт `{"status": "ok"}` всегда, если процесс отвечает."""
    return HealthResponse(status="ok", service="auth_service")


async def _ping_db() -> None:
    """Короткий `SELECT 1` через общий engine."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _collect_operational_counters() -> dict:
    """Снять operational-snapshot для observability оператора.

    Не дублирует Prometheus — даёт «увидеть в одном HTTP-запросе» (kubectl
    port-forward / curl) ключевые цифры под текущий load. Любой read-fail
    деградирует поле до 0 без поломки самого readiness — fields-best-effort.

    Поля:
      * `audit_dropped_429` — per-process счётчик из `audit_service`
        (drop после 3x429 от loging_service).
      * `failed_login_24h` — SELECT COUNT users с failed_login_attempts>0 и
        изменением за последние сутки (грубая оценка brute-force нагрузки).
      * `lockout_active` — сколько юзеров сейчас в активном lockout-окне.
    """
    from src.services import audit_service as _as

    counters: dict = {
        "audit_dropped_429": 0,
        "failed_login_24h": 0,
        "lockout_active": 0,
    }
    try:
        counters["audit_dropped_429"] = int(_as.get_dropped_429_total())
    except Exception:  # noqa: BLE001 — observability не должен валить probe
        pass

    try:
        async with AsyncSessionLocal() as session:
            # Активные lockout'ы: locked_until в будущем (NOW < locked_until).
            row = await session.execute(text(
                "SELECT COUNT(*) FROM users WHERE locked_until IS NOT NULL "
                "AND locked_until > NOW()"
            ))
            counters["lockout_active"] = int(row.scalar() or 0)
            # Failed-login activity: usability-метрика, NOT precise — берём
            # все users с активным счётчиком (sweep сбрасывает их через
            # успешный login). 24h-окно по semantics документации, но
            # столбца `failed_login_last_at` нет — используем общий счётчик.
            # TODO: добавить колонку last_failed_at и фильтр по NOW()-24h,
            # сейчас отдаём snapshot накопленных attempts > 0.
            row = await session.execute(text(
                "SELECT COUNT(*) FROM users WHERE failed_login_attempts > 0"
            ))
            counters["failed_login_24h"] = int(row.scalar() or 0)
    except Exception:  # noqa: BLE001 — счётчики best-effort
        pass

    return counters


@router.get(
    "/ready",
    summary="Readiness-проба",
    description=(
        "Readiness-чек. Пингует БД через `SELECT 1` с коротким timeout'ом. "
        "При недоступной БД отвечает 503 — k8s ingress тогда не льёт трафик в pod."
    ),
    responses={
        200: {"model": ReadyResponse, "description": "БД доступна."},
        503: {"model": ReadyResponse, "description": "БД недоступна, `reason=db_unreachable`."},
    },
)
async def readiness() -> JSONResponse:
    """Readiness — БД-пинг через short-timeout `SELECT 1`.

    На fail отдаём 503 + envelope `{"status": "not_ready", "reason": "db_unreachable"}`.
    Upstream'ы (loging_service, audit-pool) не пингуем — это observability-канал,
    не критическая зависимость для аутентификации (audit best-effort, sync fallback
    рассасывает дроп). БД — единственная hard-dependency.
    """
    try:
        await asyncio.wait_for(_ping_db(), timeout=_READINESS_TIMEOUT_SECONDS)
    except Exception as exc:
        _readiness_logger.warning("readiness probe: DB unreachable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "db_unreachable", "service": "auth_service"},
        )
    counters = await _collect_operational_counters()
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "service": "auth_service",
            "counters": counters,
        },
    )


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
        ip_address=extract_client_ip(request) if request is not None else None,
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
        401: {"description": "INVALID_CREDENTIALS — неверный username/password."},
        403: {"description": "USER_BANNED — юзер забанен; USER_BLOCKED — юзер заблокирован."},
        429: {"description": "ACCOUNT_TEMPORARILY_LOCKED — залочен после 5 неудачных попыток (lockout 15 минут)."},
    },
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Логин по паре username/password.

    Что делает:
        Проверяет пароль через Argon2id, выдаёт пару access JWT + refresh
        (opaque). На 5 неудачных подряд лочит аккаунт на 15 минут.
        Access-токен живёт 10 мин по умолчанию (`ACCESS_TOKEN_TTL_MINUTES`);
        в dev-стеке переопределён на 60 для удобства.

    Доступ:
        Публичный. Любой неаутентифицированный клиент.

    Возможные ошибки:
        * `INVALID_CREDENTIALS` (401) — неверный пароль или нет такого юзера.
        * `USER_BANNED` (403) — юзер забанен permanent/temporary.
        * `USER_BLOCKED` (403) — юзер заблокирован.
        * `ACCOUNT_TEMPORARILY_LOCKED` (429) — слишком много неудачных попыток, надо ждать.

    Связано:
        * `POST /refresh` — обновить access по refresh.
        * `POST /logout` — отозвать refresh.
        * `GET /me` — получить identity по access.
    """
    result = await auth_service.login(
        db=db,
        username=body.username,
        password=body.password,
        # `extract_client_ip` уважает trusted-proxy allow-list — тот же IP,
        # что попадает в audit-trail. Раньше Session.ip_address брался из
        # `request.client.host` напрямую и расходился с audit на ingress.
        ip_address=extract_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )
    _set_refresh_cookie(response, result.refresh_token)
    return result


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Обновить access по refresh",
    description="Меняет refresh на новую пару. Старый refresh инвалидируется атомарно.",
    response_description="Новая пара access + refresh.",
    responses={
        401: {"description": "Refresh невалидный, истёк или уже использован."},
        403: {"description": "USER_BANNED — юзер забанен; USER_BLOCKED — юзер заблокирован (сессия отзывается)."},
    },
)
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = Body(default=None),
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
        * `USER_BANNED` (403) — юзер забанен; сессия отзывается.
        * `USER_BLOCKED` (403) — юзер заблокирован; сессия отзывается.
    """
    # Body имеет приоритет над cookie — старые клиенты, которые ещё шлют
    # refresh в теле, продолжают работать. Новый UI шлёт пустой body или вовсе
    # без тела, cookie приедет на /api/auth/v1 благодаря path scope. Поэтому
    # body опционален: при отсутствии тела FastAPI не должен резать запрос 422,
    # иначе cookie-флоу недостижим.
    raw_refresh = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh:
        raise DomainValidationError(
            error_code="MISSING_REFRESH_TOKEN",
            message="refresh_token не передан ни в body, ни в cookie",
        )
    result = await auth_service.refresh(
        db=db,
        raw_refresh_token=raw_refresh,
        request_id=getattr(request.state, "request_id", None),
        ip_address=extract_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    _set_refresh_cookie(response, result.refresh_token)
    return result


@router.post(
    "/logout",
    response_model=OkResponse,
    summary="Logout — отозвать refresh",
    description="Инвалидирует переданный refresh. Access живёт до истечения TTL — короткий, 10 мин по умолчанию (в dev-стеке переопределён на 60).",
)
async def logout(
    request: Request,
    response: Response,
    body: LogoutRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Logout — отозвать refresh.

    Что делает:
        Помечает refresh как revoked в БД. Access не отзывается явно — он
        короткоживущий (10 мин), просто истечёт сам.

    Доступ:
        Публичный. Параметр — сам refresh, для несуществующего тихо ok.
    """
    raw_refresh = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh:
        # Нет refresh ни в body, ни в cookie — юзер уже фактически разлогинен
        # (например, повторный POST /logout после успешного первого, или
        # logout без cookie от свежей вкладки). Возвращаем OK и просто
        # чистим cookie, чтобы клиент не упирался в 422 на повторе.
        _clear_refresh_cookie(response)
        return OkResponse()
    await auth_service.logout(
        db=db,
        raw_refresh_token=raw_refresh,
        request_id=getattr(request.state, "request_id", None),
    )
    # Чистим cookie независимо от того, был ли refresh валидным — logout
    # идемпотентен (см. tests/auth/test_logout.py).
    _clear_refresh_cookie(response)
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
        oauth_scopes=identity.oauth_scopes,
    )


@router.patch(
    "/me",
    response_model=IdentityContext,
    summary="Self-service апдейт своего профиля",
    description=(
        "Обновляет собственный профиль: `display_name` и/или `email`. "
        "Остальные поля (`username`, `platform_role`, `department_id`, "
        "`is_banned`, `must_change_password`) запрещены — отсутствуют в "
        "схеме и режутся `extra='forbid'`."
    ),
    responses={
        401: {"description": "Нет Bearer-токена, токен невалидный, либо USER_BANNED_OR_INACTIVE — забаненный/неактивный юзер режется guard'ом раньше."},
        422: {"description": "EMPTY_UPDATE — пустое тело без полей; либо лишние поля; либо невалидный email."},
    },
)
async def patch_me(
    body: MeUpdateRequest,
    request: Request,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> IdentityContext:
    """Self-service апдейт `display_name` / `email`.

    Что делает:
        Записывает разрешённые поля юзера. Whitelist обеспечивается
        Pydantic-схемой (`extra='forbid'`) и вторым фильтром в
        `user_service.patch_me` (defence-in-depth). Возвращает свежий
        IdentityContext — UI может обновить шапку профиля без второго
        запроса.

    Доступ:
        Любой залогиненный юзер. m2m отбивается через `require_user_context`
        в зависимости. Забаненный юзер сюда не доходит — `_identity_from_user_jwt`
        бьёт 401 USER_BANNED_OR_INACTIVE раньше.

    Audit:
        `me.updated` (INFO) с изменёнными полями (email маскируется).
    """
    return await user_service.patch_me(
        db=db,
        user_id=identity.user_id,
        updates=body.model_dump(exclude_unset=True),
        request_id=getattr(request.state, "request_id", None),
    )
