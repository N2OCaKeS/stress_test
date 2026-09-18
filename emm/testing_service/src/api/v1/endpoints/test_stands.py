"""CRUD стендов (§2.3, §4 плана миграции).

Стенды — надстройка над Server/Vm из server_service, без дублирования данных.
Чтение (список/карточка) — в пределах своего отдела (`test_stands.department_id`
NOT NULL, резолвится из server_service при создании), запись
(create/update/delete) — под матрицей прав `(test_stand, *, create|update|delete)`.

`GET /test-stands/{id}` пробрасывает bearer вызывающего в server_service —
`GET /servers/{id}` там гейтит видимость по department_id caller'а, поэтому
сервисный бот-токен testing_service не годится (см. `services/server_client.py`).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import BearerToken, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.public_queue import PublicQueueItem
from src.schemas.queue import QueueItemSummaryResponse
from src.schemas.test_stand import (
    TestStandCreate,
    TestStandResponse,
    TestStandTestCredentialsResponse,
    TestStandUpdate,
)
from src.services import public_queue as public_queue_svc
from src.services import queue as queue_svc
from src.services import test_stand as svc
from src.services import log_availability

router = APIRouter(prefix="/test-stands")


@router.get(
    "",
    response_model=PaginatedResponse[TestStandResponse],
    summary="Список тестовых стендов",
    description=(
        "Стенды с опциональными фильтрами по активности/участию в очереди; "
        "выдача сужена до отдела вызывающего, `department_id` принимается "
        "только свой. Отдаёт только "
        "хранимые поля — без живого обогащения карточкой сервера (N+1 к "
        "server_service на страницу списка не делается, см. `GET /{id}`)."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — запрошен чужой `department_id`."},
    },
)
async def list_test_stands(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    department_id: str | None = Query(default=None, description="Фильтр по отделу-владельцу."),
    is_active: bool | None = Query(default=None, description="Фильтр по активности."),
    queue_enabled: bool | None = Query(default=None, description="Фильтр по участию в очереди."),
    server_id: str | None = Query(
        default=None,
        description="Фильтр по привязанному Server/Vm.id — UNIQUE, значит 0 либо 1 элемент в ответе.",
    ),
) -> PaginatedResponse[TestStandResponse]:
    """List стендов. Свой отдел."""
    items, total = await svc.list_test_stands(
        db, identity, limit=limit, offset=offset,
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id,
    )
    return PaginatedResponse[TestStandResponse](
        items=[TestStandResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=TestStandResponse,
    status_code=201,
    summary="Завести новый стенд",
    description=(
        "Регистрирует сервер/ВМ из server_service как тестовый стенд. "
        "`department_id` не принимается от клиента — резолвится живым "
        "запросом к server_service (с bearer'ом вызывающего) в момент "
        "создания. UNIQUE(server_id) — повтор → 409."
    ),
    responses={
        201: {"description": "Стенд создан."},
        403: {"description": "Нет роли с `create`, либо server_service отказал в доступе к серверу."},
        404: {"description": "Сервер не найден или не виден вызывающему."},
        409: {"description": "Этот сервер уже зарегистрирован как стенд."},
        503: {"description": "server_service недоступен или не настроен."},
    },
)
async def create_test_stand(
    body: TestStandCreate,
    identity: CurrentUserIdentity,
    bearer_token: BearerToken,
    db: AsyncSession = Depends(get_db),
) -> TestStandResponse:
    """Create стенда. Доступ: `(test_stand, *, create)`."""
    obj = await svc.create_test_stand(db, identity, bearer_token, body)
    return TestStandResponse.model_validate(obj)


@router.get(
    "/{stand_id}",
    response_model=TestStandResponse,
    summary="Получить стенд",
    description=(
        "Карточка стенда, обогащённая живой карточкой сервера из "
        "server_service (запрос идёт с bearer'ом вызывающего — та же "
        "видимость, что и при прямом обращении к server_service). Если "
        "live-вызов не удался (сервер удалён, сеть недоступна) — `server: "
        "null` и `server_unavailable: true`, весь запрос не падает."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — стенд чужого отдела."},
        404: {"description": "Стенд не найден."},
    },
)
async def get_test_stand(
    stand_id: str,
    identity: CurrentUserIdentity,
    bearer_token: BearerToken,
    db: AsyncSession = Depends(get_db),
) -> TestStandResponse:
    """Get стенда по id. Свой отдел."""
    obj, server, server_unavailable = await svc.get_test_stand(db, identity, bearer_token, stand_id)
    response = TestStandResponse.model_validate(obj)
    response.server = server
    response.server_unavailable = server_unavailable
    return response


@router.get(
    "/{stand_id}/current-queue-item",
    response_model=QueueItemSummaryResponse | None,
    summary="Активный элемент очереди этого стенда, если он сейчас есть",
    description=(
        "Не терминальный (`queued`/`preparing`/`ready`/`running`) queue_item "
        "этого стенда (§8.6 плана миграции) — сигнал консоли сервера показать "
        "кнопку «Живой лог теста» и открыть `WS /queue-items/{id}/log/stream`. "
        "`null`, если сейчас у стенда нет активной работы testing_service."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — стенд чужого отдела."},
        404: {"description": "Стенд не найден."},
    },
)
async def get_current_queue_item(
    stand_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> QueueItemSummaryResponse | None:
    """Get активного item'а очереди стенда. Свой отдел."""
    await svc.get_stand_or_404(db, stand_id, identity)
    item = await queue_svc.get_active_queue_item(db, stand_id)
    if item is None:
        return None
    return QueueItemSummaryResponse(
        queue_item_id=item.id, state=item.state, test_id=item.test_id,
        started_at=item.started_at, interrupt_action=item.interrupt_action,
    )


@router.post(
    "/{stand_id}/resume-queue",
    response_model=PublicQueueItem,
    summary="Продолжить остановленную очередь стенда",
    description=(
        "Берёт единственный `paused`-элемент стенда, переставляет его в конец "
        "очереди (`position`) и возвращает в `queued` — после чего стенд "
        "продолжает работу с того элемента, который теперь идёт первым. В "
        "ответе — сам возобновлённый элемент (его `state` уже может быть "
        "`preparing`, если очередь подхватила именно его)."
    ),
    responses={
        403: {"description": "Нет прав на этот стенд."},
        404: {"description": "Стенд не найден."},
        409: {"description": "STAND_NOT_PAUSED — у стенда нет остановленного элемента."},
    },
)
async def resume_queue(
    stand_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> PublicQueueItem:
    """Resume остановленной очереди стенда. Доступ: как у постановки в очередь."""
    item = await public_queue_svc.resume_stand_queue(db, identity, stand_id)
    logs = await log_availability.for_items(db, [item])
    return public_queue_svc.response(item).model_copy(update={"log_status": logs[item.id]})


@router.patch(
    "/{stand_id}",
    response_model=TestStandResponse,
    summary="Обновить стенд",
    description=(
        "Изменяемы только `queue_enabled`/`is_active`. Смена привязанного "
        "сервера означает создание нового стенда, не редактирование старого "
        "— `server_id` в теле PATCH игнорируется схемой."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Стенд не найден."},
    },
)
async def update_test_stand(
    stand_id: str,
    body: TestStandUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestStandResponse:
    """PATCH стенда. Доступ: `(test_stand, *, update)`."""
    obj = await svc.update_test_stand(db, identity, stand_id, body)
    return TestStandResponse.model_validate(obj)


@router.get(
    "/{stand_id}/test-credentials",
    response_model=TestStandTestCredentialsResponse,
    summary="Учётка исполнения теста стенда (живая отладка, §5.3 плана миграции)",
    description=(
        "Прокси на `GET /servers/{id}/test-credentials` server_service'а — "
        "секрет остаётся у него, здесь только pass-through его ответа с "
        "bearer'ом вызывающего. Без `?reveal=true` — только метаданные "
        "(`exists`/`username`/`ssh_public_key`/`rotated_at`). С "
        "`?reveal=true` добавляет `password_b64`/`ssh_private_key_b64` — "
        "CRITICAL-аудит `test_stand.test_credentials_revealed` (и такой же "
        "на стороне server_service), отдельный от WARNING-просмотра "
        "метаданных `test_stand.test_credentials_viewed`."
    ),
    responses={
        403: {"description": "Нет `view_test_credentials`, либо server_service отказал в доступе."},
        404: {"description": "Стенд не найден, либо сервер не найден/не виден на стороне server_service."},
        429: {"description": "RATE_LIMIT_EXCEEDED — reveal-rate-limit server_service'а пробит."},
    },
)
async def get_test_stand_credentials(
    stand_id: str,
    identity: CurrentUserIdentity,
    bearer_token: BearerToken,
    db: AsyncSession = Depends(get_db),
    reveal: bool = Query(default=False, description="Раскрыть пароль и приватный ключ."),
) -> TestStandTestCredentialsResponse:
    """Get кред тестового пользователя стенда. Доступ: `(test_stand, *, view_test_credentials)`."""
    data = await svc.get_test_stand_credentials(db, identity, bearer_token, stand_id, reveal=reveal)
    return TestStandTestCredentialsResponse(**data)


@router.delete(
    "/{stand_id}",
    response_model=OkResponse,
    summary="Удалить стенд",
    description="Hard-delete записи стенда. Сам сервер в server_service не трогается.",
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Стенд не найден."},
    },
)
async def delete_test_stand(
    stand_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete стенда. Доступ: `(test_stand, *, delete)`."""
    await svc.delete_test_stand(db, identity, stand_id)
    return OkResponse()
