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
    PowerSweepResponse,
    ProvisionStatusRequest,
    ProvisionStatusResponse,
    UsersInventoryCallbackRequest,
    UsersInventoryCallbackResponse,
    VmCreateReconcileResponse,
    VmStatusSweepResponse,
)
from src.schemas.probe_targets import ProbeTargetsResponse
from src.schemas.server import (
    AcsSnapshotCreatedCallbackRequest,
    AcsSnapshotCreatedCallbackResponse,
    AcsSnapshotRestoreDoneCallbackRequest,
    AcsSnapshotRestoreDoneCallbackResponse,
    ServerAstraUpdateCallbackRequest,
    ServerAstraUpdateCallbackResponse,
    ServerPrepareCallbackRequest,
    ServerPrepareCallbackResponse,
)
from src.schemas.vm import (
    VmDisksCallbackRequest,
    VmDisksCallbackResponse,
    VmInventoryCallbackResponse,
    VmMgmtCredentialsResponse,
    VmPackagesCallbackRequest,
    VmPackagesCallbackResponse,
    VmPreparedCallbackRequest,
    VmPreparedCallbackResponse,
    VmsHubStateCallbackRequest,
    VmsHubStateCallbackResponse,
    VmSnapshotsCallbackRequest,
    VmSnapshotsCallbackResponse,
    VmStateCallbackRequest,
    VmStateCallbackResponse,
)
from src.schemas.box import (
    BoxDownloadStateCallbackRequest,
    BoxDownloadStateCallbackResponse,
)
from src.services import box_service, internal_service

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


@router.get(
    "/accounts/{account_id}/password",
    response_model=AccountPasswordResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               404: {"description": "ACCOUNT_NOT_FOUND (нет такой учётки или чужой отдел) / ACCOUNT_HAS_NO_PASSWORD (managed без сохранённого пароля)."},
               422: {"description": "DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
               500: {"description": "ENCRYPTION_KEY_MISSING."}},
)
async def get_account_password_by_id(
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> AccountPasswordResponse:
    """Отдать пароль server_account по одному `account_id` (без server_id).

    Что делает: провижн привязанных к ВМ учёток — dispatch `vm.create` несёт
    только `account_id`/`login`, исходный сервер аккаунта воркеру неизвестен.
    Резолвит аккаунт по глобально-уникальному id, отдел берёт из карточки,
    расшифровывает и возвращает `{login, password}`.

    Доступ: `(server_account, *, view_password)`. Worker_bot роль.

    Аудит: `server_account.view_password` (WARNING на success).
    """
    data = await internal_service.fetch_account_password_by_id(
        db, identity, account_id,
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
    "/servers/power-sweep",
    response_model=PowerSweepResponse,
    responses={403: _INTERNAL_RESPONSES_BASE[403]},
)
async def power_sweep(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> PowerSweepResponse:
    """Частый прогон живой пробы питания по всем серверам: только power.status.

    Триггерится worker-scheduler'ом (`power.sweep`) по частому cron'у. В отличие
    от auto-inventory-sweep, тут нет inventory.sync и нет фильтра `is_managed`:
    ping/ssh/ipmi снимаются для ЛЮБОГО не-списанного сервера, чтобы доступность
    и питание были актуальны. Прогон платформенный, `X-Target-Department-Id` не
    требуется.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: per-server `server.power_status` (source=auto_power_sweep);
    превышение cap'а — `power_sweep.truncated`.
    """
    data = await internal_service.run_power_sweep(db, identity)
    return PowerSweepResponse(**data)


@router.post(
    "/vms/status-sweep",
    response_model=VmStatusSweepResponse,
    responses={403: _INTERNAL_RESPONSES_BASE[403]},
)
async def vm_status_sweep(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmStatusSweepResponse:
    """Частый прогон статус-пробы по всем ВМ: только vm.status.

    Триггерится worker-scheduler'ом (`vms.status_sweep`) по частому cron'у.
    Зеркало серверного power-sweep'а: воркер даёт лишь расписание, а фан-аут
    (список всех активных ВМ + dispatch `vm.status` на каждую) идёт здесь.
    `vm.status` снимает три сигнала гостя (питание domstate + ping + ssh). Прогон
    платформенный, `X-Target-Department-Id` не требуется.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: per-VM `vm.status` (source=auto_vm_status_sweep); превышение cap'а —
    `vm_status_sweep.truncated`.
    """
    data = await internal_service.run_vm_status_sweep(db, identity)
    return VmStatusSweepResponse(**data)


@router.get(
    "/probe-targets",
    response_model=ProbeTargetsResponse,
    responses={403: _INTERNAL_RESPONSES_BASE[403]},
)
async def list_probe_targets(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ProbeTargetsResponse:
    """Отдать список целей пробинга воркер-loop'ам (серверы + ВМ).

    Замена частым sweep'ам `power.sweep`/`vms.status_sweep`: вместо диспатча
    `power.status`/`vm.status` на каждую цель (task-row на каждую) server_service
    просто перечисляет цели, а воркер снимает сигналы сам из фоновых probe-циклов
    (reachability = ping+ssh, power = ipmi/domstate). Прогон платформенный,
    `X-Target-Department-Id` не требуется.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.probe_targets_listed` (только denied — при отказе гранта).
    """
    data = await internal_service.list_probe_targets(db, identity)
    return ProbeTargetsResponse(**data)


@router.post(
    "/vms/reconcile-failed-creates",
    response_model=VmCreateReconcileResponse,
    responses={403: _INTERNAL_RESPONSES_BASE[403]},
)
async def reconcile_failed_vm_creates(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmCreateReconcileResponse:
    """Reconcile упавших vm.create: удалить ВМ с терминально-провальной задачей.

    Триггерится worker-scheduler'ом (`vms.reconcile_failed_creates`) по cron'у.
    Воркер даёт только расписание; логику (ВМ в busy_state=creating, чью
    vm.create-задачу воркер завершил ошибкой → best-effort undefine + каскадное
    удаление строк + уведомление создателя) выполняет server_service. Прогон
    платформенный, `X-Target-Department-Id` не требуется.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: per-VM `vm.create_failed` (WARNING, actor = создатель ВМ).
    """
    data = await internal_service.run_vm_create_reconcile(db, identity)
    return VmCreateReconcileResponse(**data)


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
    "/servers/{server_id}/acs-snapshot-created",
    response_model=AcsSnapshotCreatedCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_acs_snapshot_created(
    server_id: str,
    body: AcsSnapshotCreatedCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> AcsSnapshotCreatedCallbackResponse:
    """Worker сообщает исход создания снимка диска через ACS (save-disk).

    Снимает `busy_state=acs → free` в любом исходе — create не переписывает
    диск, сервер свободен независимо от результата.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.acs_snapshot_created` (CRITICAL).
    """
    data = await internal_service.record_acs_snapshot_created(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return AcsSnapshotCreatedCallbackResponse(**data)


@router.post(
    "/servers/{server_id}/acs-snapshot-restore-done",
    response_model=AcsSnapshotRestoreDoneCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_acs_snapshot_restore_done(
    server_id: str,
    body: AcsSnapshotRestoreDoneCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> AcsSnapshotRestoreDoneCallbackResponse:
    """Worker сообщает исход восстановления снимка диска через ACS (restore-backup).

    При `succeeded=True` `busy_state=acs` НЕ снимается: диск переписан
    целиком, старые управляющие креды не пережили reimage, поэтому
    server_service резолвит bootstrap-пароль версии и сам диспатчит
    `server.prepare` — блокировка снимается только по завершении prepare
    (см. расширение `record_server_prepared`). При `succeeded=False`
    восстановление не состоялось — блокировка снимается сразу.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `server.acs_snapshot_restore_done` (CRITICAL).
    """
    data = await internal_service.record_acs_snapshot_restore_done(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return AcsSnapshotRestoreDoneCallbackResponse(**data)


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


# ── VM-домен: callback'и воркера ─────────────────────────────────────────────


@router.post(
    "/vms/{vm_id}/state",
    response_model=VmStateCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vm_state(
    vm_id: str,
    body: VmStateCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmStateCallbackResponse:
    """Worker пишет состояние ВМ (power/ip/status/busy_state/error).

    Идемпотентно, частичное обновление. `busy_state` снимается через
    `clear_busy_state=true`.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vm.state_updated` (INFO).
    """
    data = await internal_service.record_vm_state(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmStateCallbackResponse(**data)


@router.post(
    "/vms/{vm_id}/disks",
    response_model=VmDisksCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vm_disks(
    vm_id: str,
    body: VmDisksCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmDisksCallbackResponse:
    """Worker синкает факты дисков ВМ (state/path/target_dev/serial/size).

    Частичный, идемпотентный апдейт по disk_id. Незнакомые/удалённые диски
    пропускаются.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vm.disks_synced` (INFO).
    """
    data = await internal_service.record_vm_disks_state(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmDisksCallbackResponse(**data)


@router.post(
    "/vms/{vm_id}/snapshots",
    response_model=VmSnapshotsCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vm_snapshots(
    vm_id: str,
    body: VmSnapshotsCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmSnapshotsCallbackResponse:
    """Worker синкает снимки ВМ по имени (батч upsert + креды-по-снимку).

    Известный снимок обновляется, незнакомый заводится (worker создаёт
    `<ver>_build`/`<ver>` в ходе vm.create). Поля креды приходят plaintext'ом —
    server_service шифрует их под AAD снимка (per_snapshot). `is_current=true`
    делает снимок текущим и снимает флаг с остальных (revert).

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vm.snapshots_synced` (INFO).
    """
    data = await internal_service.record_vm_snapshots(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmSnapshotsCallbackResponse(**data)


@router.post(
    "/vms/{vm_id}/inventory",
    response_model=VmInventoryCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def receive_vm_inventory(
    vm_id: str,
    body: InventoryCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmInventoryCallbackResponse:
    """Worker отдаёт факты гостя ВМ после успешного `vm.inventory_sync`.

    VM-аналог серверного `receive_inventory`. Обновляет карточку ВМ фактами
    гостя: os_version (box-authoritative), hostname/kernel, os_last_synced_at.
    Расхождение guest-видимых vCPU со сконфигурированными НЕ перетирается —
    поднимается WARNING `vm.inventory_drift_detected`.

    Доступ: `(server, *, inventory_submit)`. Worker_bot роль (seed).

    Аудит: `vm.inventory_received` (INFO).
    """
    data = await internal_service.receive_vm_inventory(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmInventoryCallbackResponse(**data)


@router.post(
    "/vms/{vm_id}/users/inventory",
    response_model=UsersInventoryCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def receive_vm_users_inventory(
    vm_id: str,
    body: UsersInventoryCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> UsersInventoryCallbackResponse:
    """Worker отдаёт список OS-пользователей гостя ВМ после `vm.users_inventory`.

    server_service reconcile'ит его против привязанных к ВМ учёток
    (`server_account_vms`) по модели warn-on-drift: помечает present/drift,
    отдаёт незнакомых и существующих-непривязанных — БД-истину не перетирает.

    Доступ: `(server_account, *, inventory_submit)`. Worker_bot роль (seed).

    Аудит: `vm.users_inventory_received` (INFO).
    """
    data = await internal_service.receive_vm_users_inventory(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return UsersInventoryCallbackResponse(**data)


@router.post(
    "/vms/{vm_id}/packages",
    response_model=VmPackagesCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vm_packages(
    vm_id: str,
    body: VmPackagesCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmPackagesCallbackResponse:
    """Worker пишет список установленных пакетов гостя ВМ (vm.list_packages).

    Полная перезапись инвентаря (одна строка на ВМ). `GET /vms/{id}/packages`
    затем отдаёт этот список.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vm.packages_synced` (INFO).
    """
    data = await internal_service.record_vm_packages(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmPackagesCallbackResponse(**data)


@router.get(
    "/vms/{vm_id}/mgmt-credentials",
    response_model=VmMgmtCredentialsResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               404: {"description": "VM_NOT_FOUND / VM_MANAGEMENT_CREDS_NOT_FOUND (ВМ не prepared)."},
               422: {"description": "DECRYPT_FAILED — сломанный/неаутентичный ciphertext."},
               500: {"description": "ENCRYPTION_KEY_MISSING."}},
)
async def get_vm_management_credentials(
    vm_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmMgmtCredentialsResponse:
    """Отдать расшифрованные per-VM управляющие креды воркеру (зеркало серверных).

    Читает `vms.mgmt_ssh_private_key_encrypted` + `mgmt_password_encrypted`,
    расшифровывает и возвращает `{management_user, ssh_private_key, password}`.

    Доступ: `(server, *, view_management_credentials)` — тот же worker_bot-грант,
    что и у серверного mgmt-fetch'а (VM-домен переиспользует серверные гранты).

    Аудит: `vm.mgmt_credentials_revealed` (WARNING на success).
    """
    data = await internal_service.fetch_vm_management_credentials(
        db, identity, vm_id,
        target_department_id=x_target_department_id,
    )
    return VmMgmtCredentialsResponse(**data)


@router.post(
    "/vms/{vm_id}/prepared",
    response_model=VmPreparedCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vm_prepared(
    vm_id: str,
    body: VmPreparedCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmPreparedCallbackResponse:
    """Worker подтверждает, что per-VM управляющие креды установлены в госте.

    `prepared=True` → `is_managed=True`, `mgmt_creds_pending_apply=False`,
    `mgmt_creds_rotated_at=now`, снят lifecycle-lock. Опциональные plaintext-
    креды перезаписывают ciphertext под AAD ВМ.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vm.prepared` (CRITICAL).
    """
    data = await internal_service.record_vm_prepared(
        db, identity, vm_id, body,
        target_department_id=x_target_department_id,
    )
    return VmPreparedCallbackResponse(**data)


@router.post(
    "/servers/{server_id}/vms-hub-state",
    response_model=VmsHubStateCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_vms_hub_state(
    server_id: str,
    body: VmsHubStateCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> VmsHubStateCallbackResponse:
    """Worker сообщает исход подготовки сервера как VMS-hub (prepared/phy_if/error).

    `prepared=True` → `is_vms_hub=True`, `virtualization=True`, phy_if в
    network_interface_name.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `vms_hub.prepared`.
    """
    data = await internal_service.record_vms_hub_state(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return VmsHubStateCallbackResponse(**data)


@router.post(
    "/boxes/{box_id}/download-state",
    response_model=BoxDownloadStateCallbackResponse,
    responses=_INTERNAL_RESPONSES_CALLBACK,
)
async def record_box_download_state(
    box_id: str,
    body: BoxDownloadStateCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> BoxDownloadStateCallbackResponse:
    """Worker сообщает исход скачивания/импорта бокса (`box.download`).

    `status='ready'` → `download_status='ready'`, `download_last_error=NULL`;
    `status='error'` → `download_status='error'` + `download_last_error`.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Аудит: `box.download_state`.
    """
    data = await box_service.record_download_status(
        db, identity, box_id, body,
        target_department_id=x_target_department_id,
    )
    return BoxDownloadStateCallbackResponse(**data)


