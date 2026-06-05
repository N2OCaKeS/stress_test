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

Дополнительно каждый endpoint читает опциональный header
``X-Target-Department-Id`` и cross-check'ит его против реального
`server.department_id`. Worker форвардит сюда значение, которое он получил
в payload задачи (server_service сам положил `target_department_id` в
payload при dispatch'е). Soft/strict-режим — в
``internal_service._check_target_department``, контролируется
``settings.internal_require_dept_header``.

`responses=` каталог здесь публикуется как контракт worker-SDK, хотя
endpoints скрыты из OpenAPI (`include_in_schema=False`): worker
SDK-codegen всё равно читает routes и нуждается в стабильных
error_code'ах. Общий набор для всех internal-эндпоинтов:

* 403 PERMISSION_DENIED — нет нужного action в матрице.
* 403 TARGET_DEPARTMENT_MISMATCH — `X-Target-Department-Id` не совпал
  с реальным dept целевого сервера / контроллера.
* 403 TARGET_DEPARTMENT_HEADER_REQUIRED — strict-режим, header не
  прислан.
* 404 SERVER_NOT_FOUND / ACCOUNT_NOT_FOUND / NO_IPMI_CONTROLLER —
  целевой ресурс не найден.
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.internal import (
    AccountPasswordResponse,
    InventoryCallbackRequest,
    InventoryCallbackResponse,
    IpmiCredentialsResponse,
    IpmiCredentialsRotatedRequest,
    IpmiCredentialsRotatedResponse,
    PasswordRotateRequest,
    PasswordRotateResponse,
    ProvisionStatusRequest,
    ProvisionStatusResponse,
    UsersInventoryCallbackRequest,
    UsersInventoryCallbackResponse,
)
from src.schemas.server import (
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
    403: {"description": "PERMISSION_DENIED / TARGET_DEPARTMENT_MISMATCH / TARGET_DEPARTMENT_HEADER_REQUIRED."},
    404: {"description": "SERVER_NOT_FOUND / ACCOUNT_NOT_FOUND / NO_IPMI_CONTROLLER."},
}

_INTERNAL_RESPONSES_CALLBACK: dict[int | str, dict] = {
    **_INTERNAL_RESPONSES_BASE,
    422: {"description": "Битый payload (нарушение pydantic-валидации; например, BMC_VERIFY_REQUIRED / IPMI_VERIFY_TOO_OLD)."},
    500: {"description": "DECRYPT_FAILED / ENCRYPTION_KEY_MISSING / INTERNAL_ERROR."},
}

# Type alias держит дефолт FastAPI Header() аккуратным на все три route'а сразу.
_TargetDeptHeader = Header(
    default=None,
    alias="X-Target-Department-Id",
    description=(
        "Department id, который worker считает принадлежащим целевому серверу "
        "(скопирован из payload задачи). server_service cross-check'ит его с "
        "реальным server.department_id. Обязателен в strict-режиме."
    ),
)


@router.get(
    "/servers/{server_id}/ipmi/credentials",
    response_model=IpmiCredentialsResponse,
    responses={**_INTERNAL_RESPONSES_BASE,
               500: {"description": "DECRYPT_FAILED / ENCRYPTION_KEY_MISSING."}},
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
               500: {"description": "DECRYPT_FAILED / ENCRYPTION_KEY_MISSING."}},
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
    "/ipmi-controllers/{controller_id}/credentials_rotated",
    response_model=IpmiCredentialsRotatedResponse,
    responses={**_INTERNAL_RESPONSES_CALLBACK,
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


