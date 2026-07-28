"""Настраиваемая парольная политика логина под `account_admin`.

Политика пользовательских паролей (минимальная длина, обязательность
буквы/цифры) хранится singleton-строкой в БД и правится платформенным
владельцем (`account_admin`) из UI, а не задаётся в коде.

`/admin/password-policy` (GET/PUT) — только `account_admin`. Плюс публичный
`GET /password-policy` без авторизации — чтобы формы смены пароля показывали
актуальные требования (сама политика не секрет). INITIAL_ADMIN_PASSWORD этой
политике не подчиняется (жёсткий guard в коде).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin
from src.dependencies.db import get_db
from src.schemas.password_policy import (
    PasswordPolicyResponse,
    PasswordPolicyUpdate,
)
from src.services import password_policy_service as svc

router = APIRouter(prefix="/admin/password-policy", tags=["admin-password-policy"])
public_router = APIRouter(tags=["password-policy"])


@public_router.get(
    "/password-policy",
    response_model=PasswordPolicyResponse,
    summary="Текущая парольная политика логина (публично)",
    description=(
        "Read-only актуальная политика для форм ввода пароля — чтобы клиент "
        "показывал требования и не слал заведомо-422 запросы. Без авторизации; "
        "политика не секрет."
    ),
)
async def get_public_password_policy(
    db: AsyncSession = Depends(get_db),
) -> PasswordPolicyResponse:
    """Текущая политика логина. Нет строки → дефолты."""
    return await svc.get_settings(db)


@router.get(
    "",
    response_model=PasswordPolicyResponse,
    summary="Текущая парольная политика логина",
    responses={
        200: {"description": "Политика (дефолт, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ROLE_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_password_policy(
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> PasswordPolicyResponse:
    """Read-only текущая политика логина. Нет строки → дефолты."""
    return await svc.get_settings(db)


@router.put(
    "",
    response_model=PasswordPolicyResponse,
    summary="Обновить парольную политику логина",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. `min_length` в диапазоне 1..128. Изменение "
        "применяется к процессному кэшу сразу; остальные реплики подхватят "
        "через рестарт/rollout. INITIAL_ADMIN_PASSWORD этой политике не "
        "подчиняется."
    ),
    responses={
        200: {"description": "Политика обновлена."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ROLE_REQUIRED — нужна account_admin."},
        422: {"description": "min_length вне диапазона 1..128."},
    },
)
async def put_password_policy(
    payload: PasswordPolicyUpdate,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> PasswordPolicyResponse:
    """Upsert политики. Audit: `password_policy.updated`."""
    return await svc.update_settings(db, identity, payload)
