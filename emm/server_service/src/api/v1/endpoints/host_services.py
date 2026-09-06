"""Статус ASTRA/ALLTA-сервисов + control ALLTA systemd-юнитов на хосте emm.

Не путать с `host_services_settings.py` (CRUD SSH-конфига под `/settings/host-services`)
— здесь только чтение живого статуса и опасная control-операция.

`GET /host/services` — как и `/host/diskspace`, локальное самоинтроспектирование
платформы, читать может любой аутентифицированный актор. `astra_health` (внешние
HTTP + DNS) и `host_control` (ALLTA-юниты по SSH) гоняются конкурентно.

`POST /host/services/{unit}/{action}` — под `account_admin`: старт/стоп/рестарт
инфраструктурных сервисов хоста может уронить прод, тот же тир, что у ACS
settings/ротации ключей шифрования. Каждая попытка аудируется — успех, отказ
(неизвестный юнит) и сбой (SSH/guard) — CRITICAL, как `server.prepare`/`server.delete`.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException, NotFoundError
from src.dependencies.auth import AccountAdminIdentity, AuthenticatedIdentity
from src.dependencies.db import get_db
from src.schemas.host_services import HostServiceControlResult, HostServicesStatusResponse
from src.services import audit_service, astra_health, host_control

router = APIRouter(prefix="/host")


@router.get(
    "/services",
    response_model=HostServicesStatusResponse,
    summary="Статус внешних ASTRA-сервисов и ALLTA systemd-юнитов на хосте",
    description=(
        "`astra` — 4 внешних HTTP-сервиса (Jira/Life/Git/Releases) + агрегированная "
        "строка DNS, проверяются живьём. `allta` — 12 systemd-юнитов ALLTA на том же "
        "хосте, что и сам emm, статус читается по SSH; `not_configured`, если "
        "`/settings/host-services` ещё не заполнены. Обе категории — конкурентно, "
        "без DB-мутаций, доступно любому аутентифицированному актору."
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
    astra_items, allta_items = await asyncio.gather(
        astra_health.check_all(),
        host_control.get_allta_status(db),
    )
    return HostServicesStatusResponse(astra=astra_items, allta=allta_items)


@router.post(
    "/services/{unit}/{action}",
    response_model=HostServiceControlResult,
    summary="Старт/стоп/рестарт одного ALLTA systemd-юнита на хосте",
    description=(
        "Только `account_admin` — это управление инфраструктурными сервисами "
        "хоста, а не бизнес-данными отдела. `unit` — один из id `ALLTA_HOST_UNITS` "
        "(`acs`, `allta_auth`, ...); неизвестный id → 404. Команда идёт по SSH на "
        "forced-command guard хоста, который сам ограничивает допустимые юниты "
        "независимо от этого allowlist'а. Каждая попытка аудируется."
    ),
    responses={
        200: {"description": "Команда выполнена."},
        400: {"description": "HOST_SERVICES_NOT_CONFIGURED — SSH-доступ к хосту ещё не настроен."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        404: {"description": "HOST_UNIT_UNKNOWN — юнит не входит в allowlist."},
        422: {"description": "action не одно из start/stop/restart."},
        502: {"description": "HOST_SERVICE_CONTROL_FAILED — guard/systemctl на хосте отклонили команду."},
        503: {"description": "HOST_SERVICE_SSH_UNAVAILABLE — не удалось подключиться по SSH."},
    },
)
async def control_host_service(
    unit: str,
    action: Literal["start", "stop", "restart"],
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> HostServiceControlResult:
    actor = identity.username or identity.user_id
    try:
        result = await host_control.control_unit(db, unit, action)
    except NotFoundError as exc:
        audit_service.emit(
            "host_service.control",
            target_id=unit,
            target_type="host_service",
            status="denied",
            allowed=False,
            details={"action": action, "actor": actor, "reason": "unknown_unit"},
        )
        raise exc
    except AppException as exc:
        audit_service.emit(
            "host_service.control",
            target_id=unit,
            target_type="host_service",
            status="failure",
            allowed=True,
            details={"action": action, "actor": actor, "error_code": exc.error_code},
        )
        raise exc

    audit_service.emit(
        "host_service.control",
        target_id=unit,
        target_type="host_service",
        status="success",
        allowed=True,
        details={"action": action, "actor": actor, "output": result["output"]},
    )
    return HostServiceControlResult(**result)
