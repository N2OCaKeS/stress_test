"""Internal-эндпоинты для вызовов server_worker → server_service.

Скрыты из публичного OpenAPI, чтобы не утекала credential surface наружу.
Авторизация — через ту же матрицу `entity_permissions`, что и публичные
endpoint'ы: bot worker'а должен иметь роль с чувствительными actions
(`view_credentials`, `view_password`, `rotate_password`).

Дополнительно каждый endpoint читает опциональный header
``X-Target-Department-Id`` и cross-check'ит его против реального
`server.department_id`. Worker форвардит сюда значение, которое он получил
в payload задачи (server_service сам положил `target_department_id` в
payload при dispatch'е). Soft/strict-режим — в
``internal_service._check_target_department``, контролируется
``settings.internal_require_dept_header``.
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
)
from src.services import internal_service

router = APIRouter(prefix="/internal", include_in_schema=False)

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
    "/ipmi-controllers/{controller_id}/credentials_rotated",
    response_model=IpmiCredentialsRotatedResponse,
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


