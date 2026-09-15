"""Платформенные настройки доступа к ACS под `account_admin` + internal-read.

ACS — внешний сервис снимков дисков физических серверов (обёртка над
Clonezilla). Как и `/settings/probes`, это сервисная настройка уровня
платформы, а не бизнес-данные отдела: `platform_admin_guard` пропускает
`/settings/*` по allowlist'у, позитивную проверку роли делает
`require_account_admin` (`AccountAdminIdentity`).

`/settings/acs/departments` — отдельные ворота поверх обычной action-матрицы:
per-department opt-in, включает возможность делать/восстанавливать снимки
через ACS даже тем отделам, у которых есть право на действие. Сам dispatch
(create/restore) будет проверять этот флаг в следующей волне — здесь только
CRUD над флагом.

`/internal/settings/acs` — для server_worker, отдаёт `acs_url` + расшифрованный
пароль. Скрыт из публичного OpenAPI, авторизуется той же матрицей
`entity_permissions`, что и sweep/probe callback'и воркера:
`(server, *, prepare_callback)` (роль worker_bot).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.acs_settings import (
    AcsDepartmentAccessListResponse,
    AcsDepartmentAccessUpdate,
    AcsInternalSettingsResponse,
    AcsSettingsResponse,
    AcsSettingsUpdate,
    AcsCredentialOption,
)
from src.services import acs_settings as svc
from src.services import secret_client

router = APIRouter(prefix="/settings", tags=["system-settings"])


@router.get(
    "/acs",
    response_model=AcsSettingsResponse,
    summary="Текущие настройки доступа к ACS",
    responses={
        200: {"description": "Настройки (дефолт — выключено, ничего не задано, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_acs_settings(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsSettingsResponse:
    """Read-only текущие настройки ACS. Пароль не отдаётся — только `password_is_set`."""
    return await svc.get_acs_settings(db)


@router.get("/acs/credentials", response_model=list[AcsCredentialOption])
async def list_acs_credentials(identity: AccountAdminIdentity):
    """Доступные сервисные записи ACS: названия и владельцы, без значений."""
    return await secret_client.list_acs_credentials()


@router.put(
    "/acs",
    response_model=AcsSettingsResponse,
    summary="Обновить настройки доступа к ACS",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. `credential_id` ссылается на сервисную запись acs; "
        "проверенная привязка удаляет локальный пароль. Старые поля пароля "
        "принимаются только до миграции. Для включения нужны URL и учётные данные."
    ),
    responses={
        200: {"description": "Настройки обновлены."},
        400: {"description": "ACS_ENABLE_REQUIRES_CONFIG — enabled=true без acs_url/пароля."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "acs_url длиннее лимита."},
    },
)
async def put_acs_settings(
    payload: AcsSettingsUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsSettingsResponse:
    """Upsert настроек ACS. Audit: `settings.acs_updated`."""
    return await svc.update_acs_settings(db, payload)


@router.get(
    "/acs/departments",
    response_model=AcsDepartmentAccessListResponse,
    summary="Отделы с текущим флагом доступа к снимкам ACS",
    description=(
        "Список отделов-кандидатов (у кого есть сервера или у кого флаг уже "
        "выставлялся) с текущим `is_enabled`. server_service не хранит "
        "каталог отделов — имена/прочие атрибуты отдела фронтенд подтягивает "
        "из auth_service отдельно и сшивает с этим списком по `department_id`."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
    },
)
async def get_acs_department_access(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsDepartmentAccessListResponse:
    return await svc.list_acs_department_access(db)


@router.put(
    "/acs/departments",
    response_model=AcsDepartmentAccessListResponse,
    summary="Переключить доступ отдела(ов) к снимкам ACS",
    description="Один или несколько флагов `is_enabled` за раз (upsert по `department_id`).",
    responses={
        200: {"description": "Флаги обновлены, возвращён полный список."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "Пустой список items."},
    },
)
async def put_acs_department_access(
    payload: AcsDepartmentAccessUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsDepartmentAccessListResponse:
    """Upsert флагов. Audit: `settings.acs_departments_updated`."""
    return await svc.update_acs_department_access(db, payload, identity)


# ── Internal: worker читает настройки ACS ───────────────────────────────────

internal_router = APIRouter(prefix="/internal/settings", include_in_schema=False)


@internal_router.get(
    "/acs",
    response_model=AcsInternalSettingsResponse,
    responses={
        403: {"description": "PERMISSION_DENIED — нет `(server, prepare_callback)` в матрице."},
        503: {"description": "ACS_DISABLED — снимки ACS выключены или не настроены (url/пароль не заданы)."},
    },
)
async def get_acs_settings_internal(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsInternalSettingsResponse:
    """Отдать `acs_url` + расшифрованный пароль worker'у.

    Доступ: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что у `/internal/settings/probes`. Платформенный, X-Target-Department-Id
    не требуется.
    """
    return await svc.get_acs_settings_for_worker(db, identity)
