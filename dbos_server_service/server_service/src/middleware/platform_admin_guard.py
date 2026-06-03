"""Platform-admin guard middleware — отрезает platform-admin'ов от бизнес-данных.

### Зачем

Модель безопасности (§7 + §8 в репо-уровневом ``1.txt``) явно разделяет
**три плоскости администраторов**:

* ``account_admin`` — управляет платформой (пользователи, департаменты,
  сервисы); создан без департамента → не имеет сервисных ролей нигде.
* ``loging_admin`` — управляет аудитом; создан без департамента → не
  имеет сервисных ролей нигде.

``loging_reader`` НЕ блокируется: у него есть department_id (читает логи
своего отдела), и он одновременно может быть обычным сотрудником с
сервисными ролями в server_service. Доступ для него регулирует обычная
матрица прав, не middleware.

* ``department_admin`` — управляет своим департаментом, **имеет** доступ
  к бизнес-данным своего департамента.

``account_admin`` / ``loging_admin`` создаются **без департамента**
(``department_id IS NULL``) и **не могут получить ни одной сервисной роли
ни в одном прикладном сервисе**.
Если такой пользователь предъявит JWT прикладному сервису, у него будет
пустой ``service_roles`` → 403 на любом action-чеке.

Но раньше код в ``services/permissions.py`` пропускал ``account_admin`` как
global bypass матрицы — это нарушало модель: platform-admin технически мог
читать/писать business data ``server_service``. Этот middleware закрывает
дыру **до** того, как запрос вообще попадёт в endpoint-логику.

### Что блокируется

Любой запрос к ``server_service``, у которого Bearer-токен принадлежит
``platform_role ∈ {account_admin, loging_admin}``, отбивается
403 ``PLATFORM_ADMIN_BUSINESS_DATA_DENIED``. ``department_admin``
**не блокируется** — у него есть легитимный доступ к бизнес-данным своего
отдела. Сервисные роли (``reader``/``operator``/``admin``/``worker_bot``/...)
тоже **не блокируются** — это обычная rbac-плоскость.

### Что НЕ блокируется (allowlist путей)

* ``/api/server/v1/health`` — liveness probe для k8s, ходит без JWT.
* ``/api/server/v1/ready`` — readiness probe (SELECT 1 + JSON), без JWT.
* ``/openapi.json`` / ``/docs`` / ``/redoc`` — публичный Swagger в dev/test
  (в production они отключены через ``settings.app_env``, см. ``main.py``).
* Запросы без Authorization-header'а — проходят дальше (Bearer-валидация
  на уровне ``Depends(get_current_identity)`` отобьёт их 401, либо
  endpoint анонимный).

### Порядок middleware

Middleware регистрируется **после** ``attach_request_id_and_context`` и
**до** ``audit_access``/``rate_limit_middleware``:

* ``attach_request_id_and_context`` уже выставил ``request.state.request_id``
  и ``audit_context`` — нам нужен request_id, чтобы вернуть его в ошибке.
* ``audit_access`` находится «внутри» (innermost) — наш 403 на самом деле
  попадает в ``audit_access`` и эмитит ``http.access_denied`` (CRITICAL),
  плюс мы сами эмитим ``http.platform_admin_blocked`` (WARNING) с
  explicit-причиной, чтобы SIEM не мешал их с обычными permission_denied.
* ``rate_limit_middleware`` outermost — 403 platform-admin тоже идёт под
  rate-limit'ом, чтобы атакующий не мог DoS'ить audit-канал
  «platform_admin_blocked»-spam'ом.

### Что делается через introspect

Чтобы понять, ``platform_role`` ли это, middleware зовёт тот же
``_introspect()`` helper, что и обычная dependency
``get_current_identity``. Результат кладётся в
``request.state.introspect_body`` — endpoint-уровневый
``get_current_identity`` читает его оттуда и **не делает второго
roundtrip'а** к auth_service. На один защищённый запрос — один
introspect, контракт мгновенного revoke сохраняется (state не
переживает между запросами, middleware всегда зовёт свежий).

В случае любой ошибки introspect (``AuthenticationError`` / network
fail) middleware **не блокирует** — пропускает дальше. Endpoint-уровень
``CurrentIdentity`` сам отдаст 401/503 правильно. Мы не хотим
маскировать сетевые сбои под 403 platform-admin.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from starlette.responses import JSONResponse

from src.core.constants import PlatformRole
from src.core.exceptions import AppException
# Импортируем модуль целиком, а не функции, чтобы monkeypatch в тестах
# (patches `src.dependencies.auth._introspect`) реально срабатывал при
# вызове через `auth_deps._introspect(...)`. Если импортировать функции
# напрямую (`from ... import _introspect`), создаётся локальная ссылка
# на оригинал, и patch остаётся незамеченным.
from src.dependencies import auth as auth_deps
from src.services import audit_service

logger = logging.getLogger(__name__)

# Платформенные роли, которым **запрещён любой доступ** к бизнес-плоскости
# server_service. По §7 модели account_admin/loging_admin создаются без
# департамента — у них нет dept-привязки, чтобы вообще видеть business data.
#
# loging_reader НЕ блокируется: у него есть department_id (читает логи
# своего отдела), и он может одновременно быть обычным сотрудником с
# сервисными ролями в server_service. Если он не имеет сервисной роли
# в server_service — матрица прав сама отдаст 403, middleware не нужен.
#
# Set типизирован по PlatformRole-enum, а не по str — `identity.platform_role`
# уже приведён к enum через Pydantic, проверка членства идёт по enum-объектам.
# StrEnum остаётся `str`-subtype, поэтому JSON-сериализация и `in`-проверки
# по тексту работают как с обычными строками.
BLOCKED_PLATFORM_ROLES: frozenset[PlatformRole] = frozenset(
    {PlatformRole.ACCOUNT_ADMIN, PlatformRole.LOGING_ADMIN}
)

# Public-paths, которые пропускаются без introspect. Health/ready ходят
# без JWT (k8s probe), openapi/docs — публичные в dev (в production они
# отключены через docs_url=None в main.create_application).
#
# Health/ready — точный матч (тот же набор, что и в main._HEALTH_PATHS),
# чтобы гипотетический `/api/server/v1/servers/{id}/health` или иной
# вложенный путь не обходил guard через endswith.
_HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/server/v1/health",
    "/api/server/v1/ready",
})
# Полные пути публичной документации. Точное равенство по этому набору,
# никаких endswith — иначе endpoint вроде `/api/server/v1/secret/docs`
# проскочил бы whitelist по суффиксу `/docs`.
_DOCS_PATHS: frozenset[str] = frozenset({
    "/openapi.json",
    "/docs",
    "/redoc",
})
# Префиксы Swagger/Redoc static'ов. Только корневые — endpoint в любом
# другом месте, начинающийся с `/docs/...` или `/redoc/...`, у нас не
# регистрируется (router сидит под `/api/server/v1`), поэтому совпадение
# по префиксу здесь — это именно UI-ресурсы.
_DOCS_PREFIXES: tuple[str, ...] = ("/docs/", "/redoc/")


def _is_public_path(path: str) -> bool:
    """True для путей, которые middleware пропускает без проверки токена.

    Соответствует двум классам:

    * **k8s probes** (``/api/server/v1/health``, ``/api/server/v1/ready``)
      — не должны зависеть от auth_service. Матч точный, чтобы вложенные
      пути с похожим окончанием не проскочили мимо guard.
    * **OpenAPI/Swagger** (``/openapi.json``, ``/docs``, ``/redoc``, плюс
      их static-подресурсы Swagger UI) — публичные в dev/test, в проде
      отключены в ``main.py``. Сами три пути — точное равенство; статика
      — фиксированные префиксы из корня (`/docs/...`, `/redoc/...`).
    """
    if path in _HEALTH_PATHS:
        return True
    if path in _DOCS_PATHS:
        return True
    if path.startswith(_DOCS_PREFIXES):
        return True
    return False


def _build_forbidden_response(request: Request, role: PlatformRole) -> JSONResponse:
    """Стандартный envelope для ``PLATFORM_ADMIN_BUSINESS_DATA_DENIED``.

    Совместим по shape с ``app_exception_handler`` — те же поля
    (``error``/``error_code``/``message``/``details``/``request_id``/``timestamp``).
    """
    return JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "error_code": "PLATFORM_ADMIN_BUSINESS_DATA_DENIED",
            "message": (
                "Platform admins cannot access business data of "
                "server_service. See security model § 7-8."
            ),
            "details": {
                "platform_role": role,
                "blocked_platform_roles": sorted(BLOCKED_PLATFORM_ROLES),
            },
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _build_unavailable_response(request: Request) -> JSONResponse:
    """503-envelope, когда introspect-ответ не парсится в IdentityContext.

    Совместим по shape с ``app_exception_handler`` / ``ServiceUnavailableError``.
    Используется на fail-closed-пути: если introspect вернул `active=true`, но
    с неизвестным `platform_role` (rolling deploy auth_service впереди нас) —
    мы НЕ выдаём доступ, а отвечаем 503, иначе guard пробивает наверх 500.
    """
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "error_code": "AUTH_SERVICE_UNAVAILABLE",
            "message": (
                "Could not parse identity from auth_service introspect "
                "response; refusing access (fail-closed)."
            ),
            "details": {},
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def platform_admin_guard(request: Request, call_next):
    """ASGI middleware: блокирует platform-admin'ов на ВСЁМ server_service.

    Алгоритм:

    1. ``_is_public_path(path)`` → пропускаем (health/ready/openapi).
    2. Нет ``Authorization: Bearer ...`` → пропускаем; ``get_current_identity``
       сам отобьёт 401 если endpoint его требует, а анонимные endpoint'ы
       пройдут как обычно.
    3. ``_introspect(token)`` → ``identity``. Если ``_introspect`` бросает
       любое исключение (network, invalid token, и т.д.) — мы **не**
       блокируем, передаём дальше. Endpoint-уровень
       ``Depends(get_current_identity)`` сделает свой ``_introspect`` и
       отдаст пользователю правильный 401/503.
    4. ``identity.platform_role ∈ BLOCKED_PLATFORM_ROLES`` → audit + 403.
    5. Иначе → пропускаем, нормальный flow.

    Audit-event ``http.platform_admin_blocked`` (WARNING) эмитится с
    explicit-причиной — SIEM-rule «попытка platform-admin'а тронуть
    business data server_service» строится по этому action-key, не
    по generic ``http.access_denied``.
    """
    path = request.url.path
    if _is_public_path(path):
        return await call_next(request)

    token = auth_deps._extract_bearer(request)
    if token is None:
        # Анонимный запрос → пусть endpoint решает (нужен ли там auth).
        # CurrentIdentity дальше отобьёт его 401 если bearer обязателен.
        return await call_next(request)

    # Свежий introspect на каждом запросе — отозванный токен перестаёт
    # работать немедленно. Body кладём в `request.state.introspect_body`,
    # чтобы endpoint-уровень `get_current_identity` переиспользовал его
    # без второго roundtrip'а к auth_service.
    try:
        body = await auth_deps._introspect(token)
    except AppException:
        # 401 (INVALID_TOKEN_FORMAT) / 503 (AUTH_SERVICE_*) / любые наши
        # AppException-исключения — не маскируем под platform-admin блок,
        # пусть endpoint-фаза их обработает по обычному пути.
        return await call_next(request)
    except Exception:  # noqa: BLE001 — defence-in-depth: не падаем guard'ом
        # Любая непредвиденная ошибка introspect (например, JSONDecodeError
        # от шумящего auth_service) — лучше пропустить запрос, чем дать
        # false-positive «platform_admin блокирован». Endpoint-уровень
        # отдаст ServiceUnavailableError по-нормальному.
        return await call_next(request)

    # Свежий introspect получен — кладём в state, чтобы endpoint-уровень
    # `get_current_identity` переиспользовал его без второго roundtrip'а.
    # Сохраняем и при active=False (endpoint всё равно отдаст 401, но без
    # лишнего сетевого вызова) — главное, чтобы middleware и endpoint
    # видели одну и ту же картинку.
    request.state.introspect_body = body

    if not body.get("active"):
        # Невалидный/протухший токен — пусть endpoint отдаст 401
        # ACCESS_TOKEN_INVALID. Не наша забота.
        return await call_next(request)

    try:
        identity = auth_deps._to_identity(body)
    except Exception:  # noqa: BLE001 — fail-closed на неизвестной схеме identity
        # introspect отдал active=true, но тело не легло в IdentityContext —
        # чаще всего неизвестный `platform_role` (auth_service выкатили раньше
        # нас). Раньше ValidationError пробивала наверх как 500 на каждом
        # запросе таких пользователей. Не fail-open: доступ не выдаём, отвечаем
        # 503 — пусть оператор увидит несовместимость деплоев.
        logger.warning(
            "platform_admin_guard: cannot parse identity from introspect "
            "response (active token, unknown shape); responding 503"
        )
        return _build_unavailable_response(request)
    role = identity.platform_role
    if role not in BLOCKED_PLATFORM_ROLES:
        # Обычные пользователи, department_admin, worker_bot (через PAT —
        # у него service_roles, нет platform_role) — пропускаем.
        return await call_next(request)

    # Platform-admin поймал бизнес-эндпоинт. Эмитим explicit audit + 403.
    # `details.path` намеренно содержит конкретный URL — для SIEM-rule
    # «кто-то из platform_admins пробовал тронуть server_service».
    audit_service.emit(
        "http.platform_admin_blocked",
        actor_id=identity.user_id or None,
        actor_type="user",
        username=identity.username or None,
        target_type="http_endpoint",
        target_id=path,
        status="denied",
        allowed=False,
        details={
            "platform_role": role,
            "method": request.method,
            "path": path,
            "reason": "platform_admin_business_data_blocked",
        },
    )
    return _build_forbidden_response(request, role)
