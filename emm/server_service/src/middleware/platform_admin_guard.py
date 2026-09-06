"""Platform-admin guard middleware — отрезает platform-admin'ов от бизнес-данных.

### Зачем

Модель безопасности (§7 + §8 в репо-уровневом ``1.txt``) явно разделяет
**три плоскости администраторов**:

* ``account_admin`` — управляет платформой (пользователи, департаменты,
  сервисы); создан без департамента → не имеет сервисных ролей нигде.
* ``loging_admin`` — управляет аудитом; создан без департамента → не
  имеет сервисных ролей нигде.
* ``department_admin`` — управляет своим департаментом, **имеет** доступ
  к бизнес-данным своего департамента.

**``loging_reader`` — отдельная роль, middleware его НЕ блокирует:**
у него есть department_id (читает логи своего отдела), и он одновременно
может быть обычным сотрудником с сервисными ролями в server_service.
Доступ регулирует обычная матрица прав, не middleware.

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
отдела. Сервисные роли (``guest``/``admin``/``worker_bot``/кастомные)
тоже **не блокируются** — это обычная rbac-плоскость.

### Что НЕ блокируется (allowlist путей)

* ``/api/server/v1/health`` — liveness probe для k8s, ходит без JWT.
* ``/api/server/v1/ready`` — readiness probe (SELECT 1 + JSON), без JWT.
* ``/openapi.json`` / ``/docs`` / ``/redoc`` — публичный Swagger в dev/test
  (в production они отключены через ``settings.app_env``, см. ``main.py``).
* ``GET /api/server/v1/os-versions*`` — каталог OS-версий это глобальный
  справочник (имена версий, репозитории), а не бизнес-данные отдела;
  чтение публичное. Исключение только для GET — запись остаётся под
  матрицей прав. Платформенные роли теперь могут читать каталог, но не
  трогать прочие server-эндпоинты.
* ``/api/server/v1/host/services*`` — статус ASTRA/ALLTA-сервисов хоста
  (GET) и control ALLTA systemd-юнитов (POST). Не бизнес-данные отдела:
  GET — самоинтроспекция платформы, POST — управление инфраструктурой
  хоста под `account_admin`. Оба метода исключены, чтобы account_admin мог
  и видеть статус, и жать start/stop/restart на своей admin-странице.
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

from src.core.constants import HEALTH_PATHS, PlatformRole
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
# Health/ready — точный матч (источник `core/constants.HEALTH_PATHS`,
# тот же набор, что и в `main`), чтобы гипотетический
# `/api/server/v1/servers/{id}/health` или иной вложенный путь не обходил
# guard через endswith.
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

# Инфраструктурные admin-эндпоинты ротации ключей шифрования. Это явное
# исключение из business-data-блока: account_admin (платформенный владелец)
# инициирует ротацию мастер-ключа из UI, а ручки не возвращают бизнес-данные
# серверов/аккаунтов — только статус ротации и версии ключа. Сама проверка
# роли (только account_admin) живёт в `require_account_admin` на endpoint-уровне;
# guard лишь не отбивает запрос на middleware-слое. Префикс точный — вложенные
# `/admin/encryption/...` пути роутятся только сюда.
_ADMIN_ENCRYPTION_PREFIX = "/api/server/v1/admin/encryption"

# Настраиваемая парольная политика — платформенный singleton под `account_admin`,
# как и ротация ключей. Сервисная настройка уровня платформы, а не бизнес-данные
# отдела, поэтому исключение из business-блока. Позитивная проверка роли — в
# `require_account_admin` на endpoint-уровне; guard лишь не отбивает запрос.
# Префикс точный.
_ADMIN_PASSWORD_POLICY_PREFIX = "/api/server/v1/admin/password-policy"

# Конфиг управляющей учётки — платформенный singleton, которым управляет
# `account_admin` (имя управляющего пользователя + правила bootstrap'а по
# режимам ОС). Это сервисная настройка уровня платформы, а не бизнес-данные
# отдела, поэтому — как и `/admin/encryption/*` — исключение из business-блока.
# Позитивная проверка роли (только account_admin) — в `require_account_admin`
# на endpoint-уровне; guard лишь не отбивает запрос. Префикс точный.
_MANAGEMENT_USER_CONFIG_PREFIX = "/api/server/v1/management-user-config"

# Настройки проб статуса (частота ping/ssh/ipmi-опроса) — платформенный
# singleton под `account_admin`, как и конфиг управляющей учётки. Сервисная
# настройка уровня платформы, а не бизнес-данные отдела, поэтому исключение из
# business-блока. Позитивная проверка роли — в `require_account_admin` на
# endpoint-уровне; guard лишь не отбивает запрос. Префикс точный. Internal-read
# воркера живёт под `/internal/settings/*` и сюда по префиксу не попадает — его
# каллер (worker_bot) не платформенная роль, guard его и так пропускает.
_SETTINGS_PREFIX = "/api/server/v1/settings"

# Статус ASTRA/ALLTA-сервисов хоста + control (start/stop/restart) ALLTA
# systemd-юнитов. GET открыт любому аутентифицированному актору (как
# `/host/diskspace`), POST control — только `account_admin` (управление
# инфраструктурой хоста, не бизнес-данные отдела). Оба метода — исключение из
# business-блока: account_admin должен и видеть статус на своей admin-странице,
# и жать start/stop/restart. Позитивная проверка роли для control — в
# `require_account_admin` на endpoint-уровне; guard путь просто не блокирует.
_HOST_SERVICES_PREFIX = "/api/server/v1/host/services"

# Каталог OS-версий — глобальный справочник (имена версий, репозитории), а не
# бизнес-данные отдела. Чтение каталога публичное (см. endpoints/os_versions.py),
# поэтому платформенным ролям его тоже не за что отбивать. Исключение строго для
# GET-чтения: list (`/os-versions`), карточка по id (`/os-versions/{id}`) и
# по имени (`/os-versions/by-name/{name}`). POST/PATCH/DELETE сюда не попадают —
# запись остаётся под обычной проверкой матрицы прав (action `(os_version,*,...)`,
# которого у платформенных ролей нет).
_OS_VERSIONS_PREFIX = "/api/server/v1/os-versions"

# Управление матрицей прав отделов. `account_admin` — мета-админ матрицы:
# смотрит/выдаёт/снимает `entity_permissions` для любого отдела, но самих
# серверов/аккаунтов не трогает. Это исключение из business-data-блока, но
# только для account_admin: loging_admin сюда по-прежнему не пускаем (он
# управляет аудитом, не правами). Точная проверка роли — на endpoint-уровне
# (`PermissionMatrixIdentity`) и в `permission_service`; guard лишь не отбивает
# account_admin'а на этих путях. Матч по префиксу + границе сегмента, чтобы
# посторонний путь с тем же началом случайно не проскочил.
_PERMISSIONS_PREFIX = "/api/server/v1/permissions"


def _is_permissions_path(path: str) -> bool:
    """True для эндпоинтов управления матрицей прав (`/permissions*`)."""
    return path == _PERMISSIONS_PREFIX or path.startswith(
        _PERMISSIONS_PREFIX + "/"
    )


def _is_os_versions_read(path: str, method: str) -> bool:
    """True для GET-чтения каталога OS-версий.

    Матчит сам `/os-versions` (list) и любой вложенный сегмент
    `/os-versions/...` (карточка по id или by-name). Только метод GET —
    запись (POST/PATCH/DELETE) проходит обычной дорогой через матрицу прав.
    Граница сегмента обязательна, чтобы посторонний путь с тем же началом
    (например `/api/server/v1/os-versionsXYZ`) не проскочил.
    """
    if method != "GET":
        return False
    return path == _OS_VERSIONS_PREFIX or path.startswith(
        _OS_VERSIONS_PREFIX + "/"
    )


def _is_admin_encryption_path(path: str) -> bool:
    """True для инфраструктурных ручек ротации ключа (`/admin/encryption/*`).

    Эти эндпоинты доступны `account_admin`'у по дизайну (ключи шифрования —
    не бизнес-данные). Точная проверка роли — в `require_account_admin`;
    middleware их просто не блокирует. Матч по префиксу + границе сегмента,
    чтобы посторонний путь с тем же началом (например
    `/api/server/v1/admin/encryptionXYZ`) случайно не проскочил.
    """
    return path == _ADMIN_ENCRYPTION_PREFIX or path.startswith(
        _ADMIN_ENCRYPTION_PREFIX + "/"
    )


def _is_admin_password_policy_path(path: str) -> bool:
    """True для настраиваемой парольной политики (`/admin/password-policy`).

    Платформенная сервисная настройка под `account_admin` — исключение из
    business-data-блока. Точная проверка роли — в `require_account_admin`;
    middleware путь просто не блокирует. Матч по префиксу + границе сегмента.
    """
    return path == _ADMIN_PASSWORD_POLICY_PREFIX or path.startswith(
        _ADMIN_PASSWORD_POLICY_PREFIX + "/"
    )


def _is_management_user_config_path(path: str) -> bool:
    """True для конфига управляющей учётки (`/management-user-config`).

    Платформенная сервисная настройка под `account_admin` — исключение из
    business-data-блока. Точная проверка роли — в `require_account_admin`;
    middleware путь просто не блокирует. Матч по префиксу + границе сегмента.
    """
    return path == _MANAGEMENT_USER_CONFIG_PREFIX or path.startswith(
        _MANAGEMENT_USER_CONFIG_PREFIX + "/"
    )


def _is_settings_path(path: str) -> bool:
    """True для платформенных настроек проб статуса (`/settings*`).

    Сервисная настройка под `account_admin` — исключение из business-data-блока.
    Точная проверка роли — в `require_account_admin`; middleware путь просто не
    блокирует. Матч по префиксу + границе сегмента.
    """
    return path == _SETTINGS_PREFIX or path.startswith(_SETTINGS_PREFIX + "/")


def _is_host_services_path(path: str) -> bool:
    """True для статуса/control хостовых сервисов (`/host/services*`).

    GET — открыт любому аутентифицированному актору; POST control — под
    `account_admin`. Оба исключены из business-data-блока: точная проверка
    роли для control — в `require_account_admin` на endpoint-уровне, guard
    путь просто не блокирует. Матч по префиксу + границе сегмента.
    """
    return path == _HOST_SERVICES_PREFIX or path.startswith(_HOST_SERVICES_PREFIX + "/")


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
    return (
        path in HEALTH_PATHS
        or path in _DOCS_PATHS
        or path.startswith(_DOCS_PREFIXES)
        or _is_admin_encryption_path(path)
        or _is_admin_password_policy_path(path)
        or _is_management_user_config_path(path)
        or _is_settings_path(path)
        or _is_host_services_path(path)
    )


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

    1. ``_is_public_path(path)`` или ``_is_os_versions_read(path, method)``
       → пропускаем (health/ready/openapi + GET-чтение каталога OS-версий).
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
    if _is_public_path(path) or _is_os_versions_read(path, request.method):
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

    if role == PlatformRole.ACCOUNT_ADMIN and _is_permissions_path(path):
        # account_admin — мета-админ матрицы прав: управляет
        # `entity_permissions` отделов, но не бизнес-данными. Пускаем дальше,
        # роль и scope доберёт endpoint-слой + `permission_service`.
        # loging_admin сюда не попадает — он остаётся под блоком.
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
