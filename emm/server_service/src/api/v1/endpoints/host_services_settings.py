"""Per-department настройки SSH-доступа к хосту для host-service control + список юнитов.

Каждый отдел настраивает свой собственный хост: SSH-подключение (host/port/
user + приватный ключ) и список systemd-юнитов, которые он хочет видеть на
`/host/services`. Управление — `department_admin` своего отдела ИЛИ носитель
`admin` service-роли `server_service` в этом же отделе
(`services/permissions.require_host_service_action`, action
`host_service_manage`). `account_admin` доступа сюда не имеет вовсе:
`department_id` резолвится из identity, никогда не принимается параметром
запроса — cross-department peek структурно невозможен, а не просто
не-даётся-по-роли.

Дependency — `CurrentIdentity`, а не `AccountAdminIdentity`: это бизнес-данные
конкретного отдела, а не платформенная настройка (в отличие от
`/settings/acs`), позитивная проверка роли — внутри каждого handler'а через
`require_host_service_action`. `platform_admin_guard` эти пути тоже больше не
исключает (см. `middleware/platform_admin_guard.py`) — account_admin
отбивается уже на уровне `CurrentIdentity` (нет department_id/service-access),
это здесь просто defence-in-depth с понятным error_code.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.host_services_settings import (
    HostServicesSettingsResponse,
    HostServicesSettingsUpdate,
    HostServiceUnitCreate,
    HostServiceUnitListResponse,
    HostServiceUnitResponse,
    HostServiceUnitUpdate,
)
from src.services import host_services_settings as svc
from src.services import permissions

router = APIRouter(prefix="/settings", tags=["system-settings"])


def _require_department(identity) -> str:
    """`identity.department_id`, или 403 `HOST_SERVICE_NO_DEPARTMENT`.

    В нормальном flow сюда не доходят identity без department_id —
    `CurrentIdentity` уже отбивает их раньше (`SERVICE_ACCESS_DENIED`,
    пустой `allowed_services` у платформенных ролей). Явная проверка —
    defence-in-depth с понятным error_code на случай, если это когда-нибудь
    изменится.
    """
    if identity.department_id is None:
        raise AuthorizationError(
            error_code="HOST_SERVICE_NO_DEPARTMENT",
            message="Caller has no department; host-service settings are per-department business data",
        )
    return identity.department_id


@router.get(
    "/host-services",
    response_model=HostServicesSettingsResponse,
    summary="Текущие настройки SSH-доступа к хосту своего отдела",
    responses={
        200: {"description": "Настройки (дефолт — ничего не задано, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
    },
)
async def get_host_services_settings(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServicesSettingsResponse:
    """Read-only настройки своего отдела. Приватный ключ не отдаётся — только `private_key_is_set`."""
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    return await svc.get_settings(db, department_id)


@router.put(
    "/host-services",
    response_model=HostServicesSettingsResponse,
    summary="Обновить настройки SSH-доступа к хосту своего отдела",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. `ssh_private_key` — write-only, пусто = не "
        "менять; `clear_private_key=true` — явно стереть сохранённый ключ."
    ),
    responses={
        200: {"description": "Настройки обновлены."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
        422: {"description": "Невалидный порт/длина полей."},
    },
)
async def put_host_services_settings(
    payload: HostServicesSettingsUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServicesSettingsResponse:
    """Upsert настроек своего отдела. Audit: `settings.host_services_updated`."""
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    return await svc.update_settings(db, department_id, payload)


@router.get(
    "/host-services/units",
    response_model=HostServiceUnitListResponse,
    summary="Список systemd-юнитов своего отдела",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
    },
)
async def list_host_service_units(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServiceUnitListResponse:
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    return await svc.list_units(db, department_id)


@router.post(
    "/host-services/units",
    response_model=HostServiceUnitResponse,
    status_code=201,
    summary="Добавить systemd-юнит в список своего отдела",
    responses={
        201: {"description": "Юнит добавлен."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
        409: {"description": "HOST_SERVICE_UNIT_ALREADY_EXISTS — юнит с таким именем уже в списке отдела."},
        422: {"description": "HOST_SERVICE_UNIT_NAME_INVALID — имя не проходит ^[a-zA-Z0-9_.@-]+$."},
    },
)
async def create_host_service_unit(
    payload: HostServiceUnitCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServiceUnitResponse:
    """Audit: `settings.host_service_unit_added`."""
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    return await svc.create_unit(db, department_id, payload, created_by=identity.user_id)


@router.patch(
    "/host-services/units/{unit_id}",
    response_model=HostServiceUnitResponse,
    summary="Переименовать label юнита своего отдела",
    description="`unit_name` не редактируется post-creation — удалить и добавить заново.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
        404: {"description": "HOST_SERVICE_UNIT_NOT_FOUND — не существует или принадлежит другому отделу."},
    },
)
async def rename_host_service_unit(
    unit_id: str,
    payload: HostServiceUnitUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServiceUnitResponse:
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    return await svc.rename_unit(db, department_id, unit_id, payload)


@router.delete(
    "/host-services/units/{unit_id}",
    status_code=204,
    summary="Убрать юнит из списка своего отдела",
    responses={
        204: {"description": "Юнит удалён."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
        404: {"description": "HOST_SERVICE_UNIT_NOT_FOUND — не существует или принадлежит другому отделу."},
    },
)
async def delete_host_service_unit(
    unit_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Audit: `settings.host_service_unit_removed`."""
    department_id = _require_department(identity)
    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_MANAGE)
    await svc.delete_unit(db, department_id, unit_id)
