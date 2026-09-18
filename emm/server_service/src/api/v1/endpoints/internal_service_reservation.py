"""Бронь сервера «от имени сервиса» — s2s-канал под shared-secret.

Третий канал доступа к серверу, рядом с двумя существующими:

* user-facing `POST /servers/{id}/acquire` — человек, матрица
  `entity_permissions`, департаментская видимость;
* worker-internal `/internal/servers/{id}/...` — worker_bot через introspect
  и ту же матрицу;
* здесь — сервис-потребитель (`testing_service`, `acs`) через
  `X-Service-Identity` + `SERVER_INBOUND_SERVICE_API_KEYS`, тот же механизм,
  что у ops-runner'ов (`endpoints/ops.py`).

Заводить ради этого bot-пользователя в auth_service не потребовалось: у
сервисного каллера нет ни отдела, ни ролей, а держателем брони становится
имя сервиса (`servers.busy_service_name`), а не выдуманный `usr_...`.

Имя сервиса-держателя берётся ТОЛЬКО из провалидированной identity, никогда
из тела запроса — иначе `testing_service` мог бы представиться `acs` и снять
чужую бронь. По той же причине release и смена стадии проверяют, что бронь
держит именно этот caller.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    SERVICE_RESERVATION_ACS,
    SERVICE_RESERVATION_TESTING,
)
from src.dependencies.auth import require_internal_caller
from src.dependencies.db import get_db
from src.schemas.internal import (
    ServerBatchStatusRequest,
    ServerBatchStatusResponse,
    ServerConnectionInfoResponse,
    ServerStatusItem,
    ServiceAcquireRequest,
    ServiceBusyStatusRequest,
    ServiceReservationResponse,
)
from src.services import server as server_svc

# Whitelist identity, которым разрешена бронь от имени сервиса. `acs` заведён
# заранее под ретрофит снимков (сегодня server_service ставит эти поля себе
# сам, без HTTP), `testing_service` — под будущего потребителя.
_ALLOWED_IDENTITIES = (SERVICE_RESERVATION_TESTING, SERVICE_RESERVATION_ACS)

router = APIRouter(prefix="/internal/servers", include_in_schema=False)

_COMMON_RESPONSES = {
    401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
    403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
    404: {"description": "SERVER_NOT_FOUND."},
}


def _to_response(server) -> ServiceReservationResponse:
    return ServiceReservationResponse(
        server_id=server.id,
        busy_state=server.busy_state,
        busy_actor_type=server.busy_actor_type,
        busy_service_name=server.busy_service_name,
        busy_note=server.busy_note,
        busy_since=server.busy_since,
    )


@router.post(
    "/{server_id}/acquire-for-service",
    response_model=ServiceReservationResponse,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "SERVER_ALREADY_BUSY / SERVER_DECOMMISSIONED."},
    },
)
async def acquire_for_service(
    body: ServiceAcquireRequest,
    server_id: str = Path(description="ID сервера."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServiceReservationResponse:
    """Занять свободный сервер на весь цикл работы вызывающего сервиса.

    Захват атомарный (CAS по `busy_state='free'`), поэтому два сервиса не
    разберут один стенд. Занятый кем угодно — человеком или другим сервисом —
    отдаёт 409 `SERVER_ALREADY_BUSY`.

    Необязательный `requested_by_department_id` сверяется с отделом сервера;
    несовпадение маскируется под 404, как у worker-callback'ов.

    Audit: `server.acquired_for_service`.
    """
    server = await server_svc.acquire_server_for_service(
        db,
        server_id=server_id,
        service_name=caller,
        busy_state=body.busy_state,
        busy_note=body.busy_note,
        requested_by_department_id=body.requested_by_department_id,
    )
    return _to_response(server)


@router.post(
    "/{server_id}/release-for-service",
    response_model=ServiceReservationResponse,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "SERVER_NOT_BUSY / SERVER_RESERVED_BY_OTHER."},
    },
)
async def release_for_service(
    server_id: str = Path(description="ID сервера."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServiceReservationResponse:
    """Снять собственную бронь — сервер возвращается в `free`.

    Чужая бронь (человека или другого сервиса) отбивается 409
    `SERVER_RESERVED_BY_OTHER`: освобождать её вправе только её держатель.

    Audit: `server.released_for_service`.
    """
    server = await server_svc.release_server_for_service(
        db, server_id=server_id, service_name=caller,
    )
    return _to_response(server)


@router.post(
    "/{server_id}/release-for-service-as-done",
    response_model=ServiceReservationResponse,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "SERVER_NOT_BUSY / SERVER_RESERVED_BY_OTHER."},
    },
)
async def release_for_service_as_done(
    server_id: str = Path(description="ID сервера."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServiceReservationResponse:
    """Снять собственную бронь, но не отпустить сервер в `free`, а перевести
    в `testing_done`.

    Целевой сценарий — `testing_service`, когда очередь стенда опустела:
    сервис закончил работу с сервером, но кто-то должен явно посмотреть на
    стенд, прежде чем он вернётся в пул свободных. `busy_actor_type`/
    `busy_service_name`/`busy_note` не сбрасываются — они остаются
    справочным контекстом «кто тестировал» для человека, который снимет
    статус через `POST /servers/{id}/acknowledge-testing-done`.

    Чужая бронь отбивается 409 `SERVER_RESERVED_BY_OTHER`, как и у обычного
    release-for-service.

    Audit: `server.released_for_service`.
    """
    server = await server_svc.release_server_for_service_as_done(
        db, server_id=server_id, service_name=caller,
    )
    return _to_response(server)


@router.post(
    "/{server_id}/service-status",
    response_model=ServiceReservationResponse,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "SERVER_NOT_BUSY / SERVER_RESERVED_BY_OTHER."},
    },
)
async def set_service_status(
    body: ServiceBusyStatusRequest,
    server_id: str = Path(description="ID сервера."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServiceReservationResponse:
    """Переключить стадию внутри собственной брони, не отпуская сервер.

    Целевой сценарий — `acs` → `testing` в момент, когда `testing_service`
    получил креды от `prepare-for-test` и начинает SSH-сессию. `busy_since`
    остаётся прежним: он отмеряет время всей брони.

    Audit: `server.service_status_changed`.
    """
    server = await server_svc.set_service_busy_status(
        db,
        server_id=server_id,
        service_name=caller,
        busy_state=body.busy_state,
        busy_note=body.busy_note,
    )
    return _to_response(server)


@router.get(
    "/{server_id}/connection-info",
    response_model=ServerConnectionInfoResponse,
    responses=_COMMON_RESPONSES,
)
async def get_connection_info(
    server_id: str = Path(description="ID сервера."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServerConnectionInfoResponse:
    """Отдать IP стенда сервисному каллеру, у которого нет bearer'а пользователя.

    `testing_worker` получает задание на исполнение через shared-secret канал
    `testing_service`'а и физически не может пробросить чужой bearer в
    `GET /servers/{id}` (там видимость гейтится department_id держателя
    токена). IP в `test_stands` намеренно не дублируется (§4 плана миграции),
    поэтому вместо этого — узкий эндпоинт, отдающий только хост.

    Не читается как признак владения броней и ничего не проверяет по
    `busy_*` — просто справочник по id, доступный тому же whitelist'у.
    """
    server = await server_svc.get_connection_info_for_service(db, server_id=server_id)
    return ServerConnectionInfoResponse(server_id=server.id, host=str(server.ip_address))


@router.post(
    "/batch-status",
    response_model=ServerBatchStatusResponse,
    responses={401: _COMMON_RESPONSES[401], 403: _COMMON_RESPONSES[403]},
)
async def batch_status(
    body: ServerBatchStatusRequest,
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> ServerBatchStatusResponse:
    """Ping/busy пачкой для обзора пула (`testing_service`), без per-server round-trip.

    Не гейтит видимость по отделу — тот же безведомственный s2s-канал, что и
    остальные эндпоинты этого роутера (см. module docstring); вызывающий сам
    ограничивает список своими стендами. Отсутствующий id возвращается с
    `found=false`, а не выбрасывает 404 на весь батч — один снесённый сервер
    не должен ронять весь обзор пула.

    Audit не эмитится — чистое чтение существующих ping/busy сигналов, ничего
    не меняет и не раскрывает чувствительных данных.
    """
    found = await server_svc.get_batch_status_for_service(db, server_ids=body.server_ids)
    return ServerBatchStatusResponse(servers=[
        ServerStatusItem(
            server_id=server_id,
            found=server_id in found,
            busy_state=found[server_id].busy_state if server_id in found else None,
            busy_service_name=found[server_id].busy_service_name if server_id in found else None,
            ping_reachable=found[server_id].ping_reachable if server_id in found else None,
            ping_checked_at=found[server_id].ping_checked_at if server_id in found else None,
        )
        for server_id in body.server_ids
    ])
