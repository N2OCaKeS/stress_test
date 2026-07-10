"""Настраиваемая парольная политика под `account_admin` (`/admin/password-policy`).

Базовая политика для ручного ввода пароля server-аккаунтов и IPMI-credentials
(минимальная длина, обязательность буквы/цифры) хранится singleton-строкой в БД
и редактируется платформенным владельцем (`account_admin`) из UI, а не задаётся
в коде.

Как и `/admin/encryption/*` и `/settings/*`, это сервисная настройка уровня
платформы, а не бизнес-данные отдела. `platform_admin_guard` пропускает
`/admin/password-policy` по allowlist'у (`_is_admin_password_policy_path`),
позитивную проверку роли делает `require_account_admin` (`AccountAdminIdentity`).
Ручки не возвращают паролей/секретов — только саму политику.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity
from src.dependencies.db import get_db
from src.schemas.password_policy import (
    PasswordPolicyResponse,
    PasswordPolicyUpdate,
)
from src.services import password_policy_service as svc

router = APIRouter(prefix="/admin/password-policy", tags=["admin-password-policy"])


@router.get(
    "",
    response_model=PasswordPolicyResponse,
    summary="Текущая базовая парольная политика",
    responses={
        200: {"description": "Политика (дефолт, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_password_policy(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> PasswordPolicyResponse:
    """Read-only текущая базовая парольная политика. Нет строки → дефолты."""
    return await svc.get_settings(db)


@router.put(
    "",
    response_model=PasswordPolicyResponse,
    summary="Обновить базовую парольную политику",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. `min_length` в диапазоне 1..128. Изменение "
        "применяется к процессному кэшу сразу; остальные реплики подхватят "
        "через рестарт/rollout."
    ),
    responses={
        200: {"description": "Политика обновлена."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "min_length вне диапазона 1..128."},
    },
)
async def put_password_policy(
    payload: PasswordPolicyUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> PasswordPolicyResponse:
    """Upsert политики. Audit: `password_policy.updated` (WARNING)."""
    return await svc.update_settings(db, identity, payload)
