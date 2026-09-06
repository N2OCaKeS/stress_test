"""Платформенные настройки SSH-доступа к хосту для host-service control, под `account_admin`.

SSH-подключение (host/port/user + приватный ключ) к тому же физическому
хосту, где крутится сам emm, используется `host_control.py` для чтения
статуса и старта/стопа/рестарта ALLTA-юнитов. Как и `/settings/acs`, это
сервисная настройка уровня платформы, а не бизнес-данные отдела:
`platform_admin_guard` пропускает `/settings/*` по allowlist'у, позитивную
проверку роли делает `require_account_admin` (`AccountAdminIdentity`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity
from src.dependencies.db import get_db
from src.schemas.host_services_settings import (
    HostServicesSettingsResponse,
    HostServicesSettingsUpdate,
)
from src.services import host_services_settings as svc

router = APIRouter(prefix="/settings", tags=["system-settings"])


@router.get(
    "/host-services",
    response_model=HostServicesSettingsResponse,
    summary="Текущие настройки SSH-доступа к хосту (host-service control)",
    responses={
        200: {"description": "Настройки (дефолт — ничего не задано, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_host_services_settings(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServicesSettingsResponse:
    """Read-only текущие настройки. Приватный ключ не отдаётся — только `private_key_is_set`."""
    return await svc.get_settings(db)


@router.put(
    "/host-services",
    response_model=HostServicesSettingsResponse,
    summary="Обновить настройки SSH-доступа к хосту",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. `ssh_private_key` — write-only, пусто = не "
        "менять; `clear_private_key=true` — явно стереть сохранённый ключ."
    ),
    responses={
        200: {"description": "Настройки обновлены."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "Невалидный порт/длина полей."},
    },
)
async def put_host_services_settings(
    payload: HostServicesSettingsUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServicesSettingsResponse:
    """Upsert настроек. Audit: `settings.host_services_updated`."""
    return await svc.update_settings(db, payload)
