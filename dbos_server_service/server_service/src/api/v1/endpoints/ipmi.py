"""IPMI / iDRAC / iLO / Redfish — CRUD + rotate_credentials + power.

CRUD ходит через `services/ipmi_controller.py`, power — через
`worker_client.dispatch_task`. Связь servers↔ipmi_controllers 1:1
(UNIQUE на server_id), поэтому в URL `{server_id}` достаточно — controller
резолвится однозначно. GET карточки доступен по `view` или `view_credentials`;
держателю action `view_credentials` тот же GET доносит расшифрованный пароль
в `password_b64`.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    GoneError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.core.limiter import endpoint_limiter
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.dependencies.idempotency import read_idempotency_key
from src.repositories import ipmi_controller as ipmi_repo
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.ipmi_controller import (
    IpmiControllerCreate,
    IpmiControllerResponse,
    IpmiControllerUpdate,
    IpmiCredentialsRotateRequest,
    IpmiCredentialsRotateResponse,
    IpmiCredentialsViewResponse,
    IpmiPowerStatusCachedResponse,
)
from src.schemas.server import ServerTaskDispatchResponse
from src.services import audit_service, permissions, worker_client
from src.services import ipmi_controller as ipmi_svc
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error

# Соответствие Action → action-key в audit. Держим явный словарь, а не строим
# имя из enum'а — переименование enum'а не должно ломать стабильные SIEM-ключи.
_POWER_ACTION_TO_AUDIT = {
    Action.POWER_ON: "server.power_on",
    Action.POWER_OFF: "server.power_off",
    Action.POWER_REBOOT: "server.power_reboot",
}

# Общий каталог ответов для трёх power-эндпоинтов. Путь идёт через
# `_dispatch_power → worker_client.dispatch_task_with_hit`, поэтому набор
# error_code одинаков; держим в одной константе, чтобы ветки не разъезжались.
_POWER_RESPONSES: dict[int | str, dict] = {
    202: {"description": "Задача принята, возвращается task_id для отслеживания."},
    400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
    403: {"description": "Нет роли с соответствующим power-action либо чужой department."},
    404: {"description": "Сервер не найден / чужой dept (скрыто за 404) либо NO_IPMI_CONTROLLER."},
    409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
    503: {"description": "Worker недоступен (WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED)."},
}

router = APIRouter(prefix="/servers/{server_id}/ipmi")
# Отдельный router для list — без server_id в prefix'е.
# Каноничный путь — `/ipmi-controllers` (kebab-case, как остальные
# коллекции сервиса: `/server-accounts`, `/os-versions`,
# `/ipmi-controllers/{id}` POST/rotate). Старый `/ipmi_controllers`
# (underscore) оставлен алиасом ради совместимости с уже задеплоенным
# клиентом — оба роутера привязаны к одному handler'у ниже.
list_router = APIRouter(prefix="/ipmi-controllers")
list_router_legacy = APIRouter(prefix="/ipmi_controllers", include_in_schema=False)


async def _dispatch_power(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    server_id: str,
    action: str,
    task_kind: str,
) -> dict:
    """Общая логика для POST /power/{on,off,reboot}.

    Порядок проверок (важен для безопасности):

      1. `require_action(POWER_*)` — если нет права на конкретную power-операцию.
         Идёт первой по канону permission → visibility: `require_action`
         смотрит только на роли caller'а и его department, никаких данных о
         целевом сервере не использует, поэтому 403 на этом шаге не делает
         existence-oracle. Симметрия с `get_server`/`update_server`/
         `delete_server` и server_account.* CRUD.
      2. `get_server` — visibility + dept-isolation. Cross-dept или non-existent
         → 404.
      3. `SERVER_DECOMMISSIONED` — нельзя дёргать выведенный сервер.
      4. `NO_IPMI_CONTROLLER` (404) — нет записи в `ipmi_controllers`
         (BMC не настроен). Без этой проверки worker получит таску и упадёт
         уже в runtime — нарушает контракт «202 = задача принята и физически
         выполнима». Унифицирован между power/get/rotate.
      5. `dispatch_task` — INSERT в `dev_server_worker.tasks` + `.kiq()` в Redis.

    Любая ветка denied/failure пишет explicit audit-event ДО raise. Без этого
    попытка guest'а делает power-cycle падала бы только в generic
    `http.access_denied` middleware'а — SIEM не отличил бы её от любой 403.
    """
    audit_action = _POWER_ACTION_TO_AUDIT[action]
    # canon: permission first, visibility second.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, action)
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        # NotFoundError — cross-dept или несуществующий сервер: business not-found,
        # caller прошёл permission, но цель невидима — `failure`/`allowed=True`.
        # AuthorizationError — нет VIEW (роль с POWER_* без VIEW): `denied`/`allowed=False`.
        # Любая ветка пишет audit, иначе guest/cross-dept attempts уходят без следа.
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept power operations",
        )
    # IPMI-controller presence gate. Power-операция обязана идти через BMC
    # (iDRAC/iLO/IPMI/Redfish), и worker без записи `ipmi_controllers`
    # (endpoint_url + username + password_encrypted) задачу выполнить не
    # сможет — без этой проверки server_service отправлял бы таску в Redis,
    # клиент получал бы 202+task_id, и проблема всплыла бы только в worker'е
    # как «failed at runtime», ломая контракт «202 = принята и выполнима».
    #
    # Проверяем БД-row: данные BMC лежат в отдельной таблице `ipmi_controllers`
    # с UNIQUE на server_id (1:1 relation). Row нет — 404 NO_IPMI_CONTROLLER
    # (target IPMI controller отсутствует, как отсутствующий ресурс) +
    # failure-audit. Унифицирован с rotate/get_status, чтобы клиент не угадывал
    # по коду, какой именно из IPMI-эндпоинтов он дёргает.
    ipmi_ctrl = await ipmi_repo.get_by_server_id(db, server_id)
    if ipmi_ctrl is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "no_ipmi", "department_id": server.department_id},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="Server has no IPMI controller configured (BMC endpoint/credentials missing)",
        )
    # Idempotency-Key — опциональный HTTP-header (стандарт IETF
    # draft-ietf-httpapi-idempotency-key). Если клиент его прислал —
    # пробрасываем в worker_client, чтобы dispatch_task мог дедупнуть
    # повторный POST (retry/двойной клик/redrive) и вернуть тот же task_id.
    idempotency_key = read_idempotency_key(request)
    # ConflictError(TASK_IDEMPOTENT_CONFLICT) и ServiceUnavailableError
    # (WORKER_DB_NOT_CONFIGURED / WORKER_REDIS_NOT_CONFIGURED /
    # UNKNOWN_TASK_KIND) поднимаются из worker_client.dispatch_task и без
    # явного перехвата сюда улетают в app-exception handler — клиент
    # получит envelope с error_code, но в loging_service ничего, что
    # ломает инвариант «любая попытка power-операции → audit-событие».
    # Поэтому эмитим failure ДО re-raise.
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server_id,
            # `target_department_id` нужен worker'у чтобы echo'нуть этот dept в
            # `X-Target-Department-Id` header при вызове internal credential
            # endpoints. server_service на той стороне cross-check'ит header
            # против реального `server.department_id` (defense-in-depth от
            # cross-tenant probes через worker-PAT — см. docstring internal_service).
            payload={"server_id": server_id, "target_department_id": server.department_id},
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except ConflictError:
        # TASK_IDEMPOTENT_CONFLICT — два POST'а с одним Idempotency-Key
        # успели гонкой пройти SELECT и упасть на UNIQUE-constraint, а
        # повторный SELECT всё ещё ничего не нашёл (см. worker_client
        # dispatch_task docstring). Редкая, но реальная коллизия —
        # клиент получит 409, мы фиксируем failure-аудит.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        # Worker недоступен (нет SERVER_WORKER_DATABASE_URL /
        # SERVER_WORKER_REDIS_URL, либо task_kind не зарегистрирован в
        # broker stubs). Клиент получит 503, мы фиксируем failure —
        # оператору видна попытка + причина инфраструктурного фейла.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return {"task_id": task_id, "status": "queued"}


# ── CRUD endpoints ───────────────────────────────────────────────────────────


@list_router.get(
    "",
    response_model=None,
    summary="Список IPMI-контроллеров, видимых вызывающему",
    description=(
        "Возвращает страницу IPMI-контроллеров серверов своего отдела. "
        "JOIN с `servers` обеспечивает department-фильтр (контроллеры "
        "наследуют dept от связанного сервера). Без `view` на "
        "`ipmi_controller` — 403.\n\n"
        "Два режима пагинации: cursor (`cursor=true` или `after=<token>`, "
        "envelope `{items, next_cursor, has_more}`) и legacy offset/limit "
        "(envelope `{items, total, limit, offset}`)."
    ),
    responses={
        200: {"description": "Страница контроллеров."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
        401: {"description": "Нет/невалидный bearer-токен."},
        403: {"description": "Нет роли с `view` на ipmi_controller."},
    },
)
async def list_controllers(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope."),
) -> PaginatedResponse[IpmiControllerResponse] | CursorPaginatedResponse[IpmiControllerResponse]:
    """List-эндпоинт. Доступ: `(ipmi_controller, *, view)`."""
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError, to_bad_request
        try:
            items, next_cursor, has_more = await ipmi_svc.list_controllers_cursor(
                db, identity, limit=limit, after=after,
            )
        except InvalidCursorError as exc:
            raise to_bad_request(exc) from exc
        return CursorPaginatedResponse[IpmiControllerResponse](
            items=[IpmiControllerResponse.model_validate(i) for i in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )
    items, total = await ipmi_svc.list_controllers(
        db, identity, limit=limit, offset=offset
    )
    return PaginatedResponse[IpmiControllerResponse](
        items=[IpmiControllerResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# Legacy alias `GET /ipmi_controllers` (snake_case) проксирует на тот же
# handler; скрыт из OpenAPI и оставлен для существующих клиентов до их
# миграции на kebab-case путь.
list_router_legacy.add_api_route(
    "",
    list_controllers,
    methods=["GET"],
    response_model=None,
    include_in_schema=False,
)


@router.post(
    "",
    response_model=IpmiControllerResponse,
    status_code=201,
    summary="Зарегистрировать IPMI-контроллер для сервера (1:1)",
    description=(
        "Создаёт запись BMC для сервера. Пароль шифруется через "
        "`secrets_service.encrypt()`. Server должен существовать и "
        "принадлежать своему department'у. UNIQUE(server_id) → "
        "повторная регистрация для того же сервера → 409 IPMI_DUPLICATE."
    ),
    responses={
        201: {"description": "IPMI-контроллер создан."},
        403: {"description": "Нет `create` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "У сервера уже есть IPMI-контроллер (UNIQUE)."},
    },
)
async def create_controller(
    server_id: str,
    body: IpmiControllerCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> IpmiControllerResponse:
    """Create-эндпоинт. Доступ: `(ipmi_controller, *, create)`."""
    obj = await ipmi_svc.create_controller(db, identity, server_id, body)
    return IpmiControllerResponse.model_validate(obj)


@router.get(
    "",
    response_model=IpmiControllerResponse,
    summary="Карточка IPMI-контроллера (с паролем при наличии view_credentials)",
    description=(
        "Возвращает kind/endpoint_url/username. Если у вызывающего есть "
        "`view_credentials`, поле `password_b64` несёт base64(plaintext); "
        "иначе оно `null`. Cross-dept сервер скрыт за 404 SERVER_NOT_FOUND. "
        "Сервер без контроллера → 404 NO_IPMI_CONTROLLER. Раскрытие пароля пишет "
        "CRITICAL audit `ipmi_controller.credentials_revealed`."
    ),
    responses={
        403: {"description": "Нет ни `view`, ни `view_credentials`."},
        404: {"description": "Сервер не найден / чужой dept, либо контроллер не зарегистрирован."},
        500: {"description": "DECRYPT_FAILED — сломанный ciphertext (только при view_credentials)."},
    },
)
async def get_controller(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> IpmiControllerResponse:
    """Get-эндпоинт. Доступ: `(ipmi_controller, *, view)`; пароль — при `view_credentials`."""
    obj, password_b64 = await ipmi_svc.get_controller(db, identity, server_id)
    resp = IpmiControllerResponse.model_validate(obj)
    resp.password_b64 = password_b64
    return resp


@router.patch(
    "",
    response_model=IpmiControllerResponse,
    summary="Обновить IPMI-контроллер (без пароля)",
    description=(
        "Частичное обновление: `kind` / `endpoint_url` / `username`. "
        "Смена пароля — через `POST /credentials/rotate` (отдельный "
        "CRITICAL audit-event)."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Сервер не найден / чужой dept, либо контроллер не зарегистрирован."},
    },
)
async def update_controller(
    server_id: str,
    body: IpmiControllerUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> IpmiControllerResponse:
    """Update-эндпоинт. Доступ: `(ipmi_controller, *, update)`."""
    obj = await ipmi_svc.update_controller(db, identity, server_id, body)
    return IpmiControllerResponse.model_validate(obj)


@router.delete(
    "",
    response_model=OkResponse,
    summary="Удалить IPMI-контроллер (CRITICAL аудит)",
    description=(
        "Hard-delete BMC-записи. После удаления power-операции на сервере "
        "будут отбиваться 404 NO_IPMI_CONTROLLER до повторной регистрации. "
        "Только роль с `delete`."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Сервер не найден / чужой dept, либо контроллер не зарегистрирован."},
    },
)
async def delete_controller(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete-эндпоинт. Доступ: `(ipmi_controller, *, delete)`. Аудит — CRITICAL."""
    await ipmi_svc.delete_controller(db, identity, server_id)
    return OkResponse()


@router.post(
    "/credentials/rotate",
    response_model=IpmiCredentialsRotateResponse,
    summary="Ротация IPMI-пароля (legacy: 410 GONE для user, bot-only fallback)",
    description=(
        "User-facing вызовы (`subject_type != 'bot'`) отбиваются 410 GONE с "
        "CRITICAL-аудитом: endpoint писал plaintext в `password_encrypted` "
        "БЕЗ apply/verify на BMC, что могло убить out-of-band доступ.\n\n"
        "Канонический путь ротации:\n"
        "- инициируется через `POST /api/server/v1/ipmi-controllers/{id}/rotate` "
        "  (worker dispatch с BMC apply);\n"
        "- worker по завершении ходит во внутренний callback "
        "  `POST /internal/ipmi-controllers/{id}/credentials_rotated`, который "
        "  проверяет свежесть `verified_at` против `IPMI_VERIFY_MAX_AGE_SECONDS` "
        "  и только тогда шифрует и сохраняет ciphertext.\n\n"
        "Этот endpoint оставлен исключительно как fallback для bot-токенов "
        "(`subject_type='bot'`) — backwards-compat для уже задеплоенных "
        "worker'ов до их миграции на internal callback. Сам он verify-proof "
        "НЕ проверяет — у новых интеграций должен быть путь через internal."
    ),
    responses={
        403: {"description": "Нет `rotate_credentials`."},
        404: {"description": "Сервер не найден / чужой dept, либо контроллер не зарегистрирован."},
        410: {"description": "User-facing endpoint снят; используйте `/ipmi-controllers/{id}/rotate`."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP rotate-rate-limit пробит."},
    },
)
@endpoint_limiter.limit(get_settings().ipmi_credentials_rotate_rate_limit)
async def rotate_credentials(
    request: Request,
    server_id: str,
    identity: CurrentIdentity,
    body: IpmiCredentialsRotateRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> IpmiCredentialsRotateResponse:
    """Rotate-эндпоинт. Доступ: `(ipmi_controller, *, rotate_credentials)`. Аудит — CRITICAL.

    Контракт после P0-фикса:

    * `subject_type != 'bot'` → 410 GONE + CRITICAL-audit. User-facing путь
      убран, потому что писал ciphertext без apply/verify на BMC и мог
      разорвать out-of-band доступ. Caller должен переехать на
      `POST /ipmi-controllers/{id}/rotate` (worker dispatch).
    * `subject_type == 'bot'` → fallback для legacy worker-токенов: пишет
      ciphertext в БД без verify-proof. Новые worker'ы должны ходить через
      internal callback `record_ipmi_credentials_rotated`, который ПРОВЕРЯЕТ
      свежесть `verified_at` и только потом сохраняет.
    """
    if identity.subject_type != "bot":
        audit_service.emit(
            "ipmi_controller.rotate_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="warning", allowed=False,
            details={
                "reason": "user_facing_endpoint_deprecated",
                "server_id": server_id,
                "caller_type": identity.subject_type,
                "migration": "use POST /ipmi-controllers/{id}/rotate",
            },
        )
        # details пустые: эхо `server_id` из URL даёт CAS-different (caller
        # может подсунуть UPPER/Lower/whitespace вариант), а полезной
        # информации не несёт — caller сам знает, что он передал. Audit
        # выше пишет сырой server_id уже как security-trail.
        raise GoneError(
            error_code="IPMI_ROTATE_USER_FACING_DEPRECATED",
            message=(
                "User-facing /ipmi/credentials/rotate is deprecated: it stored "
                "plaintext without BMC apply/verify. Use "
                "POST /api/server/v1/ipmi-controllers/{id}/rotate (worker "
                "dispatch with BMC apply + verify)."
            ),
        )
    new_password = body.password if body is not None else None
    obj = await ipmi_svc.rotate_credentials(db, identity, server_id, new_password)
    return IpmiCredentialsRotateResponse(
        id=obj.id,
        rotated_at=obj.password_rotated_at,
    )


# ── User-facing read-only views ──────────────────────────────────────────────


@router.get(
    "/credentials",
    response_model=IpmiCredentialsViewResponse,
    summary="Метаданные IPMI-credentials (БЕЗ plaintext-пароля)",
    description=(
        "Возвращает kind / endpoint_url / username / `password_rotated_at` — "
        "без plaintext-пароля. Plaintext доступен только worker'у через "
        "internal endpoint `/internal/servers/{id}/ipmi/credentials`. "
        "Доступ: `(ipmi_controller, *, view_credentials)`."
    ),
    responses={
        200: {"description": "Метаданные controller'а."},
        403: {"description": "Нет роли с `view_credentials`."},
        404: {"description": "Сервер не найден / чужой dept, либо нет IPMI-контроллера."},
    },
)
async def view_credentials(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> IpmiCredentialsViewResponse:
    """
    Что делает: SELECT IPMI-controller по server_id, возвращает метаданные
    без password. Симметрия с `endpoints/internal.py::get_ipmi_credentials` —
    тот же permission action, но без расшифровки.

    Доступ: `(ipmi_controller, *, view_credentials)`. По умолчанию выдан только
    admin-role и worker_bot (см. seed_dev / 43cf9cfef9e1 миграция).

    Аудит: `ipmi_controller.view_credentials_meta` — INFO (метаданные, не сам секрет).
    """
    audit_action = "ipmi_controller.view_credentials_meta"
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="ipmi_controller",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS,
        )
    # worker_bot не имеет `server.view`, поэтому здесь только visibility-check
    # без require_action на VIEW — основное право уже снято через VIEW_CREDENTIALS.
    try:
        server = await server_svc.load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    ctrl = await ipmi_repo.get_by_server_id(db, server_id)
    if ctrl is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "not_registered", "department_id": server.department_id},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="No IPMI controller is registered for this server",
        )
    audit_service.emit(
        audit_action, target_id=ctrl.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={"server_id": server_id, "department_id": server.department_id},
    )
    return IpmiCredentialsViewResponse.model_validate(ctrl)


@router.get(
    "/power",
    response_model=IpmiPowerStatusCachedResponse,
    summary="Кэшированное состояние питания (без live BMC-probe)",
    description=(
        "Читает `servers.power_state` + (если есть BMC) `last_probed_at` из "
        "`ipmi_controllers`. Без обращения к worker'у — это cached-view для "
        "dashboard'ов. TTL у поля нет: значение перетирается worker'ом при "
        "очередном `power.{on,off,reboot}` callback'е, между обновлениями "
        "может быть сколь угодно устаревшим. Live-опрос доступности — "
        "`GET /servers/{id}/power-status` (TCP-ping SSH-порта, см. "
        "worker_dispatch). Доступ: `(server, *, view)` — это чтение поля "
        "`servers.power_state`, требует только VIEW; `power_status` "
        "(live-probe) дёргается через worker_dispatch."
    ),
    responses={
        200: {"description": "Кэшированный power_state сервера."},
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Сервер не найден / чужой dept."},
    },
)
async def power_status(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> IpmiPowerStatusCachedResponse:
    """
    Что делает: возвращает кэшированный power_state без обращения к BMC.
    Поле `servers.power_state` пишется worker'ом и здесь только читается —
    TTL/инвалидации нет, freshness ограничен только частотой
    `power.status`-probe'ов.

    Доступ: `(server, *, view)` — поле `servers.power_state` читается под
    общим VIEW. Department-isolation через `load_visible_server`.

    Аудит: `server.power_status_cached` (INFO) на success/denied.
    """
    audit_action = "server.power_status_cached"
    # Кэшированный power_state — это чтение поля servers.power_state, поэтому
    # требуем `view` (а не `power_status`, который для live-probe через worker'а).
    # `guest` без view → 403; reader/operator/admin с view → 200.
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.VIEW,
        )
    except AuthorizationError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    try:
        server = await server_svc.load_visible_server(db, identity, server_id)
    except NotFoundError:
        # Visibility-404: caller прошёл VIEW-permission, цель просто не видна
        # (несуществующий id или cross-dept). `failure`/`allowed=True`, не `denied` —
        # права не отказаны, конвенция симметрична остальным visibility-сайтам.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    ctrl = await ipmi_repo.get_by_server_id(db, server_id)
    last_probed_at = ctrl.last_probed_at if ctrl is not None else None
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": server.department_id,
            "power_state": server.power_state,
        },
    )
    return IpmiPowerStatusCachedResponse(
        server_id=server_id,
        power_state=server.power_state,
        last_probed_at=last_probed_at,
    )


# ── Power operations ─────────────────────────────────────────────────────────


@router.post(
    "/power/on",
    response_model=ServerTaskDispatchResponse,
    summary="Включить питание (ставит задачу worker'у, 202)",
    status_code=202,
    description=(
        "Публикует задачу `power.on` в taskiq-broker. Synchronous-валидации "
        "перед dispatch'ем: server существует, dept совпадает, "
        "ServerStatus != DECOMMISSIONED, есть запись в `ipmi_controllers`. "
        "Header `Idempotency-Key` (опционально) — повторный POST с тем же "
        "ключом вернёт тот же `task_id`."
    ),
    responses=_POWER_RESPONSES,
)
async def power_on(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """
    Что делает: ставит задачу `power.on` в очередь worker'а.

    Доступ: `(server, *, power_on)`.

    Возможные ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND,
    404 NO_IPMI_CONTROLLER, 409 SERVER_DECOMMISSIONED,
    409 TASK_IDEMPOTENT_CONFLICT, 503 WORKER_UNREACHABLE.

    Связано: `_dispatch_power`, `worker_client.dispatch_task`,
    `server_worker/tasks/power.py`.
    """
    result = await _dispatch_power(
        db=db, identity=identity, request=request, server_id=server_id,
        action=Action.POWER_ON, task_kind="power.on",
    )
    return ServerTaskDispatchResponse(**result)


@router.post(
    "/power/off",
    response_model=ServerTaskDispatchResponse,
    summary="Выключить питание hard (ставит задачу worker'у, 202)",
    status_code=202,
    description=(
        "Аналог power/on, но задача `power.off`. Питание срезается hard — "
        "worker всегда отправляет `ResetType: ForceOff` (Redfish) / "
        "`chassis power off` (ipmitool). Soft/ACPI shutdown через этот "
        "endpoint не поддерживается. Тот же набор валидаций и кодов."
    ),
    responses=_POWER_RESPONSES,
)
async def power_off(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """
    Что делает: ставит задачу `power.off` в очередь worker'а. Worker всегда
    делает hard power-off (`ForceOff` / `chassis power off`), без graceful.

    Доступ: `(server, *, power_off)`.
    Ошибки и связи — как у `power_on`.
    """
    result = await _dispatch_power(
        db=db, identity=identity, request=request, server_id=server_id,
        action=Action.POWER_OFF, task_kind="power.off",
    )
    return ServerTaskDispatchResponse(**result)


@router.post(
    "/power/reboot",
    response_model=ServerTaskDispatchResponse,
    summary="Перезагрузить (ставит задачу worker'у, 202)",
    status_code=202,
    description=(
        "Power-cycle через BMC (обычно reset через iDRAC/Redfish, не soft-reboot). "
        "Тот же набор валидаций, что у power/on."
    ),
    responses=_POWER_RESPONSES,
)
async def power_reboot(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """
    Что делает: ставит задачу `power.reboot` в очередь worker'а.

    Доступ: `(server, *, power_reboot)`.
    Ошибки и связи — как у `power_on`.
    """
    result = await _dispatch_power(
        db=db, identity=identity, request=request, server_id=server_id,
        action=Action.POWER_REBOOT, task_kind="power.reboot",
    )
    return ServerTaskDispatchResponse(**result)
