"""Конфиг управляющей учётки — платформенный singleton под `account_admin`.

Сервисная настройка уровня платформы (имя управляющего пользователя + правила
bootstrap'а по режимам ОС), а не бизнес-данные отдела. Поэтому управляет ею
платформенный владелец `account_admin`, как и ротацией ключей шифрования.
`platform_admin_guard` пропускает эти пути по allowlist'у
(`_is_management_user_config_path`), а позитивную проверку роли делает
`require_account_admin` (`AccountAdminIdentity`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity
from src.dependencies.db import get_db
from src.schemas.management_user_config import (
    ManagementUserConfigResponse,
    ManagementUserConfigUpdate,
)
from src.services import management_user_config as svc

router = APIRouter(prefix="/management-user-config", tags=["management-user-config"])


@router.get(
    "",
    response_model=ManagementUserConfigResponse,
    summary="Текущий конфиг управляющей учётки",
    responses={
        200: {"description": "Конфиг со всеми режимами (дефолт, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_management_user_config(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> ManagementUserConfigResponse:
    """Read-only текущий конфиг. Нет строки → дефолт (login=dbos, пустые режимы)."""
    return await svc.get_config(db)


@router.put(
    "",
    response_model=ManagementUserConfigResponse,
    summary="Заменить/обновить конфиг управляющей учётки",
    description=(
        "Обновляет имя управляющей учётки и пер-режимные настройки bootstrap'а. "
        "`login` без значения — без изменений; присланные режимы в `modes` "
        "заменяются целиком, остальные сохраняются.\n\n"
        "Смена `login` потенциально требует cutover на всех серверах — здесь "
        "значение только сохраняется, а в ответе поднимается `login_changed` "
        "(+ `previous_login`). Сам rename/фан-аут — отдельная фаза."
    ),
    responses={
        200: {"description": "Конфиг обновлён."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "Невалидный login / группы / пустые команды."},
    },
)
async def put_management_user_config(
    payload: ManagementUserConfigUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> ManagementUserConfigResponse:
    """Полная замена/обновление конфига. Audit: `management_user_config.update`."""
    return await svc.update_config(db, payload)
