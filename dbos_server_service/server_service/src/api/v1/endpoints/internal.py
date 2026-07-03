"""Internal-эндпоинты для вызовов server_worker → server_service.

Скрыты из публичного OpenAPI, чтобы не утекала credential surface наружу.
Авторизация — через ту же матрицу `entity_permissions`, что и публичные
endpoint'ы: bot worker'а должен иметь роль с чувствительными actions
(`view_credentials`, `view_password`, `rotate_password`).

`subject_type == 'bot'` на FastAPI-уровне НЕ enforce'ится (owner-decision
2026-05-30 — полагаемся на матрицу прав): worker_bot — единственная роль,
которой по seed выданы `(server_account, view_password)`,
`(server_account, rotate_password)`, `(ipmi_controller, view_credentials)`
и `(ipmi_controller, rotate_credentials)`. Любой каллер с такой комбинацией
действий формально пройдёт. Симметрия с user-facing
`POST /servers/{id}/ipmi/credentials/rotate` сломана: тот endpoint явно
отбивается 410 GONE для не-bot subject_type (см. `endpoints/ipmi.py`),
здесь же subject_type не проверяется. Документировано как трейд-офф
до появления выделенного worker-PAT-канала.

Каждый endpoint читает заголовок ``X-Target-Department-Id`` и cross-check'ит
его против реального `server.department_id`. Worker форвардит сюда значение,
которое он получил в payload задачи (server_service сам положил
`target_department_id` в payload при dispatch'е, уже проверив права юзера).
Отдел самого воркер-бота в авторизации не участвует — он глобальный. Проверка
заголовка безусловна (см. ``internal_service._check_target_department``).

`responses=` каталог здесь публикуется как контракт worker-SDK, хотя
endpoints скрыты из OpenAPI (`include_in_schema=False`): worker
SDK-codegen всё равно читает routes и нуждается в стабильных
error_code'ах. Общий набор для всех internal-эндпоинтов:

* 403 PERMISSION_DENIED — нет нужного action в матрице.
* 403 TARGET_DEPARTMENT_HEADER_REQUIRED — `X-Target-Department-Id` не
  прислан.
* 404 SERVER_NOT_FOUND / ACCOUNT_NOT_FOUND / NO_IPMI_CONTROLLER —
  целевой ресурс не найден. Сюда же маскируется target_department_mismatch
  (`X-Target-Department-Id` не совпал с реальным dept целевого сервера /
  контроллера) — отдаётся 404 той же маски, чтобы 403/404 не работали
  enumeration-oracle'ом для воркера, щупающего чужой отдел; deny-аудит при
  этом эмитится с `reason=target_department_mismatch`.
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.internal import (
    AccountPasswordResponse,
    AutoInventorySweepResponse,
    InventoryCallbackRequest,
    InventoryCallbackResponse,
    IpmiCredentialsResponse,
    IpmiCredentialsRotatedRequest,
    IpmiCredentialsRotatedResponse,
    ManagementCredentialsResponse,
    ManagementCredsAppliedResponse,
    PasswordRotateRequest,
    PasswordRotateResponse,
    PowerStateCallbackRequest,
    PowerStateCallbackResponse,
    ProvisionStatusRequest,
    ProvisionStatusResponse,
    UsersInventoryCallbackRequest,
    UsersInventoryCallbackResponse,
)
from src.schemas.server import (
    ServerAstraUpdateCallbackRequest,
    ServerAstraUpdateCallbackResponse,
    ServerPrepareCallbackRequest,
    ServerPrepareCallbackResponse,
)
from src.services import internal_service

router = APIRouter(prefix="/internal", include_in_schema=False)

# Общий каталог responses для всех internal-эндпоинтов. Шарится между
# read-endpoint'ами (`get_*`) и callback'ами (`record_*`); добавление 409 в
# конкретный handler делается локально (например, `record_ipmi_credentials_rotated`
# имеет специфичный CREDENTIALS_ALREADY_APPLIED / BMC_VERIFY_REQUIRED).
_INTERNAL_RESPONSES_BASE: dict[int | str, dict] = {
    403: {"description": "PERMISSION_DENIED (нет action'а в матрице) / TARGET_DEPARTMENT_HEADER_REQUIRED (`X-Target-Department-Id` не прислан). target_department_mismatch отдаётся 404, а не 403 — см. ниже."},
    404: {"description": "SERVER_NOT_FOUND / ACCOUNT_NOT_FOUND / NO_IPMI_CONTROLLER. Сюда же маскируется target_department_mismatch — `X-Target-Department-Id` не совпал с server.department_id, в audit пишется `reason=target_department_mismatch`."},
}

_INTERNAL_RESPONSES_CALLBACK: dict[int | str, dict] = {
    **_INTERNAL_RESPONSES_BASE,
    422: {"description": "Битый payload (нарушение pydantic-валидации) либо DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
    500: {"description": "ENCRYPTION_KEY_MISSING / INTERNAL_ERROR."},
}

# Type alias держит дефолт FastAPI Header() аккуратным на все три route'а сразу.
_TargetDeptHeader = Header(
    default=None,
    alias="X-Target-Department-Id",
    description=(
        "Department id, который worker считает принадлежащим целевому серверу "
        "(скопирован из payload задачи). server_service cross-check'ит его с "
        "реальным server.department_id. Обязателен: без него — 403."
    ),
)


@router.get(
    "/servers/{server_id}/ipmi/credentials",
    response_model=IpmiCredentialsResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               422: {"description": "DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
               500: {"description": "ENCRYPTION_KEY_MISSING."}},
)
async def get_ipmi_credentials(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> IpmiCredentialsResponse:
    """Отдать расшифрованные IPMI-credentials для server_worker.

    Что делает: проверяет `view_credentials` на IPMI_CONTROLLER, читает
    `ipmi_controllers.password_encrypted`, расшифровывает и возвращает
    `{kind, endpoint_url, username, password}`.

    Доступ: `(ipmi_controller, *, view_credentials)`. По соглашению — только
    роль `worker_bot` (least-privilege, 4 grants: view_credentials,
    rotate_credentials, view_password, rotate_password).

    Аудит: `ipmi_controller.view_credentials` (WARNING на success).
    """
    data = await internal_service.fetch_ipmi_credentials(
        db, identity, server_id,
        target_department_id=x_target_department_id,
    )
    return IpmiCredentialsResponse(**data)


@router.get(
    "/servers/{server_id}/accounts/{account_id}/password",
    response_model=AccountPasswordResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               409: {"description": "ACCOUNT_HAS_NO_PASSWORD — discovered-аккаунт без сохранённого ciphertext."},
               422: {"description": "DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
               500: {"description": "ENCRYPTION_KEY_MISSING."}},
)
async def get_account_password(
    server_id: str,
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> AccountPasswordResponse:
    """Отдать расшифрованный пароль server_account для worker'а.

    Что делает: проверяет `view_password` на SERVER_ACCOUNT, читает
    `server_accounts.password_encrypted`, расшифровывает и возвращает
    `{login, password}`.

    Доступ: `(server_account, *, view_password)`. Worker_bot роль.

    Аудит: `server_account.view_password` (WARNING на success).
    """
    data = await internal_service.fetch_account_password(
        db, identity, server_id, account_id,
        target_department_id=x_target_department_id,
    )
    return AccountPasswordResponse(**data)


@router.post(
    "/servers/{server_id}/accounts/{account_id}/password/rotate",
    response_model=PasswordRotateResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def rotate_account_password(
    server_id: str,
    account_id: str,
    body: PasswordRotateRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> PasswordRotateResponse:
    """Принять новый пароль server_account от worker'а (после SSH-apply).

    Что делает: проверяет `rotate_password`, шифрует `body.password` через
    `secrets_service.encrypt()`, апдейтит `server_accounts.password_encrypted`
    и `password_rotated_at`.

    Доступ: `(server_account, *, rotate_password)`. Worker_bot роль.

    Аудит: `server_account.rotate_password` (CRITICAL severity).
    """
    data = await internal_service.rotate_account_password(
        db, identity, server_id, account_id, body.password,
        target_department_id=x_target_department_id,
    )
    return PasswordRotateResponse(**data)


@router.get(
    "/servers/{server_id}/management/credentials",
    response_model=ManagementCredentialsResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               404: {"description": "SERVER_NOT_FOUND / MANAGEMENT_CREDS_NOT_FOUND (сервер не prepared)."},
               422: {"description": "DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
               500: {"description": "ENCRYPTION_KEY_MISSING."}},
)
async def get_management_credentials(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> ManagementCredentialsResponse:
    """Отдать расшифрованные per-server управляющие креды для server_worker (#3).

    Что делает: проверяет `view_management_credentials` на SERVER, читает
    `servers.mgmt_ssh_private_key_encrypted` + `mgmt_password_encrypted`,
    расшифровывает и возвращает `{management_user, ssh_private_key, password}`.
    Пока `mgmt_creds_pending_apply=True` и есть previous-материал — отдаёт его
    (рабочий на боксе), иначе текущий.

    Доступ: `(server, *, view_management_credentials)`. Worker_bot роль.

    Аудит: `server.management_credentials_revealed` (WARNING на success).
    """
    data = await internal_service.fetch_management_credentials(
        db, identity, server_id,
        target_department_id=x_target_department_id,
    )
    return ManagementCredentialsResponse(**data)


@router.post(
    "/servers/{server_id}/management-credentials/applied",
    response_model=ManagementCredsAppliedResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_management_creds_applied(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> ManagementCredsAppliedResponse:
    """Worker сообщает, что новые управляющие креды применены на боксе (rotate, #3).

    server_service снимает `mgmt_creds_pending_apply`, зануляет previous-зеркала
    и проставляет `mgmt_creds_rotated_at`. С этого момента fetch отдаёт текущий
    материал.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.management_creds_rotated` (CRITICAL).
    """
    data = await internal_service.confirm_management_creds_applied(
        db, identity, server_id,
        target_department_id=x_target_department_id,
    )
    return ManagementCredsAppliedResponse(**data)


# ── Worker → server_service callbacks ───────────────────────────────────────


@router.post(
    "/servers/{server_id}/inventory",
    response_model=InventoryCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def receive_inventory(
    server_id: str,
    body: InventoryCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> InventoryCallbackResponse:
    """Worker отдаёт hardware-facts после успешного `inventory.sync`.

    Доступ: `(server, *, inventory_submit)`. Worker_bot роль (seed).

    Аудит: `server.inventory_received` (INFO).
    """
    data = await internal_service.receive_inventory(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return InventoryCallbackResponse(**data)


@router.post(
    "/servers/{server_id}/users/inventory",
    response_model=UsersInventoryCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def receive_users_inventory(
    server_id: str,
    body: UsersInventoryCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> UsersInventoryCallbackResponse:
    """Worker отдаёт список реальных OS-пользователей после `users.inventory`.

    server_service reconcile'ит его против привязанных к серверу
    `server_accounts`: создаёт discovered-аккаунты для новых, обновляет
    метаданные существующих, помечает drift у пропавших.

    Доступ: `(server_account, *, inventory_submit)`. Worker_bot роль (seed).

    Аудит: `server_account.users_inventory_received` (INFO).
    """
    data = await internal_service.receive_users_inventory(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return UsersInventoryCallbackResponse(**data)


@router.post(
    "/servers/{server_id}/accounts/{account_id}/provision_status",
    response_model=ProvisionStatusResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_provision_status(
    server_id: str,
    account_id: str,
    body: ProvisionStatusRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> ProvisionStatusResponse:
    """Worker сообщает результат useradd/usermod/userdel на боксе.

    server_service обновляет `present_on_server` на связке аккаунт ↔ сервер:
    provision/update → True, deprovision → False.

    Доступ: `(server_account, *, provision_on_host)`. Worker_bot роль (seed).

    Аудит: `server_account.provision_status` (INFO).
    """
    data = await internal_service.record_provision_status(
        db, identity, server_id, account_id, body,
        target_department_id=x_target_department_id,
    )
    return ProvisionStatusResponse(**data)


@router.post(
    "/servers/{server_id}/power-state",
    response_model=PowerStateCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_power_state(
    server_id: str,
    body: PowerStateCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> PowerStateCallbackResponse:
    """Worker пишет результат живой пробы питания (`power.status`) в кэш сервера.

    server_service проставляет `servers.power_state` + источник
    (`power_state_source`) и момент приёма (`power_state_checked_at`, UTC). До
    этого callback'а поле никогда не обновлялось — UI всегда видел unknown.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.power_state_updated` (INFO).
    """
    data = await internal_service.record_power_state(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return PowerStateCallbackResponse(**data)


@router.post(
    "/servers/auto-inventory-sweep",
    response_model=AutoInventorySweepResponse,
    responses={403: _INTERNAL_RESPONSES_BASE[403]},
)
async def auto_inventory_sweep(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> AutoInventorySweepResponse:
    """Плановый прогон: inventory.sync + power.status по всем managed-серверам.

    Триггерится worker-scheduler'ом (`auto_inventory.sweep`) по cron'у. Воркер
    даёт только расписание; фан-аут (список managed + dispatch каждой пары
    задач) идёт здесь, через штатный `worker_client` — воркер не дублирует БД
    server_service. Прогон платформенный, не привязан к отделу: заголовок
    `X-Target-Department-Id` не требуется.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: per-server `server.inventory_sync` / `server.power_status`
    (source=auto_scheduled); превышение cap'а — `auto_inventory_sweep.truncated`.
    """
    data = await internal_service.run_auto_inventory_sweep(db, identity)
    return AutoInventorySweepResponse(**data)


@router.post(
    "/servers/{server_id}/prepared",
    response_model=ServerPrepareCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_server_prepared(
    server_id: str,
    body: ServerPrepareCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> ServerPrepareCallbackResponse:
    """Worker сообщает, что бутстрап управления сервера завершён.

    server_service помечает сервер подготовленным: `is_managed=True`,
    `prepared_at=now`, `management_user=<имя>`.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.prepared` (CRITICAL).
    """
    data = await internal_service.record_server_prepared(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return ServerPrepareCallbackResponse(**data)


@router.post(
    "/servers/{server_id}/astra-updated",
    response_model=ServerAstraUpdateCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_server_astra_updated(
    server_id: str,
    body: ServerAstraUpdateCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> ServerAstraUpdateCallbackResponse:
    """Worker сообщает исход обновления ОС (astra_update).

    Снимает updating-блокировку сервера. При `succeeded=True` также привязывает
    сервер к целевой версии ОС и запускает inventory.sync; при `succeeded=False`
    версию не трогает.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.astra_updated`.
    """
    data = await internal_service.record_server_astra_updated(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return ServerAstraUpdateCallbackResponse(**data)


@router.post(
    "/ipmi-controllers/{controller_id}/credentials_rotated",
    response_model=IpmiCredentialsRotatedResponse,
    responses={**_INTERNAL_RESPONSES_CALLBACK,
               400: {"description": "ROTATED_AT_IN_FUTURE / ROTATED_AT_TOO_OLD (rotated_at вне NTP-окна) либо BMC_VERIFY_REQUIRED (отсутствует/протух proof успешного BMC test-call'а)."},
               409: {"description": "CREDENTIALS_ALREADY_APPLIED — callback на не-pending row."}},
)
async def ipmi_credentials_rotated_callback(
    controller_id: str,
    body: IpmiCredentialsRotatedRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> IpmiCredentialsRotatedResponse:
    """Worker сообщает что rotate IPMI-credentials прошёл (отдаёт plaintext).

    Симметрия с account-rotate'ом: worker присылает plaintext через TLS
    внутри cluster'а, server_service шифрует и сохраняет — у worker'а нет
    `SERVER_ENCRYPTION_KEY`. Storage-first ordering обязывает worker'а
    дёрнуть этот endpoint ДО PATCH'а BMC; иначе при сбое worker'а между
    BMC-apply и storage-save доступ к iDRAC потерян безвозвратно.

    Доступ: `(ipmi_controller, *, rotate_credentials)`. Worker_bot роль (seed).

    Аудит: `ipmi_controller.credentials_rotated_callback` (WARNING).
    """
    data = await internal_service.record_ipmi_credentials_rotated(
        db, identity, controller_id, body,
        target_department_id=x_target_department_id,
    )
    return IpmiCredentialsRotatedResponse(**data)


