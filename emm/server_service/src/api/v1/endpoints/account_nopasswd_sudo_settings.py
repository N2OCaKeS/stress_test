"""NOPASSWD sudo для тестовых учёток — per-department opt-in.

`server_account.has_sudo=True` сегодня только добавляет учётку в группу
`sudo` — обычная политика Debian/Astra (`%sudo ALL=(ALL:ALL) ALL`) всё равно
спрашивает пароль на каждый вызов. Для отделов, у которых сервер/ВМ — это
одноразовый управляемый тестовый контур, а не чья-то рабочая станция, это
неудобно; другим отделам это может быть и не нужно (более консервативная
политика на своих боксах) — опция per-department, дефолт `False`.

Два входа на одну таблицу (`AccountNopasswdSudoSettings`):

* `/settings/account-nopasswd-sudo` — self-service: department_admin своего
  отдела или носитель `admin` service-роли server_service в этом же отделе
  (`permissions.require_department_nopasswd_sudo_action`, переиспользует
  action `grant_sudo` — тот же уровень privilege-эскалации). `account_admin`
  сюда обычным путём попасть не может: `CurrentIdentity` требует
  `server_service` в `allowed_services`, а у платформенного `account_admin`
  его нет вовсе.
* `/settings/account-nopasswd-sudo/departments` — оверсайт: `account_admin`
  видит и правит флаг любого отдела списком/батчем, тот же паттерн, что и
  `/settings/acs/departments`.

Сам флаг читает server_worker dispatch-payload builder
(`worker_dispatch._build_account_task_payload` / `_build_vm_account_task_payload`
/ `server.prepare`'s linked_accounts, `vm.create`'s accounts) — не отдельным
internal-эндпоинтом, а прямым вызовом
`account_nopasswd_sudo_settings.is_enabled_for_department` в том же db-сеансе.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity, CurrentIdentity
from src.dependencies.db import get_db
from src.core.exceptions import AuthorizationError
from src.schemas.account_nopasswd_sudo_settings import (
    AccountNopasswdSudoSettingsBatchUpdate,
    AccountNopasswdSudoSettingsListResponse,
    AccountNopasswdSudoSettingsResponse,
    AccountNopasswdSudoSettingsUpdate,
)
from src.services import account_nopasswd_sudo_settings as svc
from src.services import permissions

router = APIRouter(prefix="/settings", tags=["system-settings"])


def _require_department(identity) -> str:
    """`identity.department_id`, или 403 `ACCOUNT_NOPASSWD_SUDO_NO_DEPARTMENT`.

    Зеркало `host_services_settings._require_department`: в нормальном flow
    сюда не доходят identity без department_id (`CurrentIdentity` уже отбивает
    их раньше), явная проверка — defence-in-depth с понятным error_code.
    """
    if identity.department_id is None:
        raise AuthorizationError(
            error_code="ACCOUNT_NOPASSWD_SUDO_NO_DEPARTMENT",
            message="Caller has no department; this setting is per-department business data",
        )
    return identity.department_id


@router.get(
    "/account-nopasswd-sudo",
    response_model=AccountNopasswdSudoSettingsResponse,
    summary="Текущий флаг NOPASSWD sudo своего отдела",
    responses={
        200: {"description": "Флаг (дефолт — false, ничего не задано, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / ACCOUNT_NOPASSWD_SUDO_NO_DEPARTMENT / PERMISSION_DENIED."},
    },
)
async def get_account_nopasswd_sudo_settings(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> AccountNopasswdSudoSettingsResponse:
    department_id = _require_department(identity)
    await permissions.require_department_nopasswd_sudo_action(db, identity, department_id)
    return await svc.get_own(db, department_id)


@router.put(
    "/account-nopasswd-sudo",
    response_model=AccountNopasswdSudoSettingsResponse,
    summary="Включить/выключить NOPASSWD sudo для своего отдела",
    description=(
        "Пока выключено (дефолт), sudo-аккаунты (`has_sudo=True`) на серверах/ВМ "
        "отдела продолжают спрашивать пароль на каждый sudo-вызов (обычная группа "
        "`sudo`). Включение начинает класть per-user NOPASSWD sudoers-правило "
        "новым provision/prepare-вызовам; существующие учётки подхватят его на "
        "следующем provision/update/apply, автоматического fan-out'а нет. "
        "Выключение симметрично: worker больше не кладёт новое правило, но уже "
        "поставленное на существующих учётках НЕ снимается автоматически — "
        "требуется явный provision/update/deprovision этой учётки (либо ручная "
        "чистка `/etc/sudoers.d/<login>-nopasswd` на боксе)."
    ),
    responses={
        200: {"description": "Флаг обновлён."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / ACCOUNT_NOPASSWD_SUDO_NO_DEPARTMENT / PERMISSION_DENIED."},
    },
)
async def put_account_nopasswd_sudo_settings(
    payload: AccountNopasswdSudoSettingsUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> AccountNopasswdSudoSettingsResponse:
    """Upsert флага своего отдела. Audit: `settings.account_nopasswd_sudo_updated`."""
    department_id = _require_department(identity)
    await permissions.require_department_nopasswd_sudo_action(db, identity, department_id)
    return await svc.update_own(db, department_id, payload, identity)


@router.get(
    "/account-nopasswd-sudo/departments",
    response_model=AccountNopasswdSudoSettingsListResponse,
    summary="Отделы с текущим флагом NOPASSWD sudo (оверсайт)",
    description=(
        "Список отделов-кандидатов (у кого есть сервера или у кого флаг уже "
        "выставлялся) с текущим `is_enabled`. server_service не хранит "
        "каталог отделов — имена фронтенд подтягивает из auth_service "
        "отдельно и сшивает с этим списком по `department_id`."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
    },
)
async def list_account_nopasswd_sudo_departments(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AccountNopasswdSudoSettingsListResponse:
    return await svc.list_all(db)


@router.put(
    "/account-nopasswd-sudo/departments",
    response_model=AccountNopasswdSudoSettingsListResponse,
    summary="Переключить флаг NOPASSWD sudo отдела(ов) (оверсайт)",
    description="Один или несколько флагов `is_enabled` за раз (upsert по `department_id`).",
    responses={
        200: {"description": "Флаги обновлены, возвращён полный список."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "Пустой список items."},
    },
)
async def put_account_nopasswd_sudo_departments(
    payload: AccountNopasswdSudoSettingsBatchUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> AccountNopasswdSudoSettingsListResponse:
    """Upsert флагов. Audit: `settings.account_nopasswd_sudo_updated`."""
    return await svc.update_many(db, payload, identity)
