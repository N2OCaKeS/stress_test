"""Статус ASTRA-сервисов + статус/control systemd-юнитов на отдельском хосте.

Не путать с `host_services_settings.py` (CRUD SSH-конфига/юнитов под
`/settings/host-services*`) — здесь только чтение живого статуса и опасная
control-операция.

`GET /host/services` — `astra` не изменился: платформенный, внешний, читать
может любой аутентифицированный актор (`AuthenticatedIdentity`, без
department/service-access гейта — как и раньше). `allta` теперь per-department:
своих юнитов caller видит без отдельного permission-чека (это его же отдела
данные — `identity.department_id` неявно и есть весь скоуп), платформенные
роли (`department_id is None`) получают пустой список — им попросту нечего
резолвить, не потому что мы их прячем. Обе категории гоняются конкурентно.

Обе категории идут через TTL-кэш со stale-while-revalidate
(`astra_health.check_all_cached`, `host_control.get_allta_status_cached`) —
страница открывается мгновенно из последнего известного результата, живой
поход (HTTP к внешним сервисам / SSH на хост отдела) происходит в фоне,
когда кэш протухает, а не на каждый рендер.

`POST /host/services/{unit_id}/{action}` — теперь под
`require_host_service_action(..., Action.HOST_SERVICE_CONTROL)`:
`department_admin` своего отдела или носитель `admin` service-роли
`server_service` в своём отделе. `unit_id` резолвится в конкретную строку
`HostServiceUnit`, принадлежность отделу — часть резолва (см.
`host_control.control_unit`). Каждая попытка аудируется — успех, отказ
(неизвестный/чужой юнит) и сбой (SSH/guard) — CRITICAL, как
`server.prepare`/`server.delete`.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action
from src.core.exceptions import AppException, AuthorizationError, NotFoundError
from src.dependencies.auth import AuthenticatedIdentity, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.host_services import HostServiceControlResult, HostServicesStatusResponse
from src.services import audit_service, astra_health, host_control, permissions

router = APIRouter(prefix="/host")


@router.get(
    "/services",
    response_model=HostServicesStatusResponse,
    summary="Статус внешних ASTRA-сервисов и systemd-юнитов своего отдела на хосте",
    description=(
        "`astra` — 4 внешних HTTP-сервиса (Jira/Life/Git/Releases) + агрегированная "
        "строка DNS, проверяются живьём, платформенные, видны всем. `allta` — юниты, "
        "которые СВОЙ отдел caller'а сам добавил в список (`/settings/host-services/units`), "
        "статус читается по SSH на хост этого отдела; `[]`, если у отдела нет ни "
        "SSH-конфига, ни единого юнита в списке, или у caller'а вообще нет department_id "
        "(платформенная роль). Обе категории — конкурентно, без DB-мутаций."
    ),
    responses={
        200: {"description": "Статус обеих категорий (деградирует поэлементно, никогда не 5xx)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
    },
)
async def get_host_services(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServicesStatusResponse:
    if identity.department_id is None:
        # account_admin/loging_admin — этот GET единственный host-services
        # путь, который `platform_admin_guard` для них не блокирует (см.
        # middleware docstring). У них нет department_id, юниты резолвить
        # не из чего — `allta` пуст без единого SSH-вызова.
        astra_items = await astra_health.check_all_cached()
        allta_items = []
    else:
        astra_items, allta_items = await asyncio.gather(
            astra_health.check_all_cached(),
            host_control.get_allta_status_cached(db, identity.department_id),
        )
    return HostServicesStatusResponse(astra=astra_items, allta=allta_items)


@router.post(
    "/services/{unit_id}/{action}",
    response_model=HostServiceControlResult,
    summary="Старт/стоп/рестарт одного systemd-юнита своего отдела на хосте",
    description=(
        "`department_admin` своего отдела или носитель `admin` service-роли "
        "`server_service` в своём отделе (`host_service_control`). `unit_id` — "
        "id строки `HostServiceUnit` из списка СВОЕГО отдела caller'а; чужой или "
        "несуществующий id → 404, неотличимо снаружи. Команда идёт по SSH на "
        "forced-command guard хоста этого отдела, который сам ограничивает "
        "допустимые юниты независимо от этой проверки. Каждая попытка аудируется."
    ),
    responses={
        200: {"description": "Команда выполнена."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "SERVICE_ACCESS_DENIED / HOST_SERVICE_NO_DEPARTMENT / PERMISSION_DENIED."},
        404: {"description": "HOST_UNIT_UNKNOWN — юнит не существует либо принадлежит другому отделу."},
        422: {"description": "action не одно из start/stop/restart."},
        503: {"description": "HOST_SERVICES_NOT_CONFIGURED — SSH-доступ к хосту отдела ещё не настроен."},
        502: {"description": "HOST_SERVICE_CONTROL_FAILED — guard/systemctl на хосте отклонили команду."},
    },
)
async def control_host_service(
    unit_id: str,
    action: Literal["start", "stop", "restart"],
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServiceControlResult:
    actor = identity.username or identity.user_id
    if identity.department_id is None:
        raise AuthorizationError(
            error_code="HOST_SERVICE_NO_DEPARTMENT",
            message="Caller has no department; host-service control is per-department business data",
        )
    department_id = identity.department_id

    await permissions.require_host_service_action(db, identity, department_id, Action.HOST_SERVICE_CONTROL)

    try:
        result = await host_control.control_unit(db, department_id, unit_id, action)
    except NotFoundError as exc:
        audit_service.emit(
            "host_service.control",
            target_id=unit_id,
            target_type="host_service",
            status="denied",
            allowed=False,
            details={"department_id": department_id, "action": action, "actor": actor, "reason": "unknown_unit"},
        )
        raise exc
    except AppException as exc:
        audit_service.emit(
            "host_service.control",
            target_id=unit_id,
            target_type="host_service",
            status="failure",
            allowed=True,
            details={"department_id": department_id, "action": action, "actor": actor, "error_code": exc.error_code},
        )
        raise exc

    audit_service.emit(
        "host_service.control",
        target_id=unit_id,
        target_type="host_service",
        status="success",
        allowed=True,
        details={"department_id": department_id, "action": action, "actor": actor, "output": result["output"]},
    )
    return HostServiceControlResult(**result)
