"""Internal s2s read-эндпоинты loging_service.

Скрыты из публичного OpenAPI. Авторизация — `require_service_token`
(`SERVICE_API_KEYS` map + `X-Service-Identity`), та же, что и на ingest
(`POST /events`). Нужны, чтобы доверенный сервис мог читать собственный
срез аудита без user-bearer'а с ролью `loging_admin`/`loging_reader`.

Сейчас единственный потребитель — `server_service`: drift-агрегация
(`GET /api/server/v1/servers/{id}/drift`) собирает события
`server_account.drift_detected` по своим серверам. Раньше этот путь
ходил в публичный `GET /events`, который требует user-bearer и отбивал
service-key 401 → drift отдавал 503. Теперь чтение идёт сюда, под тем же
shared-secret, что и write-канал.

Фильтр обязателен: caller должен сузить выборку (`service` + `action`),
чтобы internal-канал не превращался в неограниченный дамп журнала под
service-key'ом.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.constants import Severity
from src.core.limits import MAX_QUERY_LIMIT, MAX_QUERY_OFFSET
from src.dependencies.auth import require_service_token
from src.dependencies.db import get_db
from src.schemas.events import EventListResponse
from src.services import event_service
from src.utils.normalization import normalize_identifier

router = APIRouter(
    prefix="/internal",
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)


@router.get("/events", response_model=EventListResponse)
def list_events_internal(
    db: Session = Depends(get_db),
    department_id: str | None = Query(default=None),
    service: str | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    action: str | None = Query(default=None),
    actor_id: str | None = Query(default=None, max_length=48),
    target_id: str | None = Query(default=None, max_length=48),
    status_filter: Literal["success", "failure", "denied", "warning"] | None = Query(
        default=None, alias="status",
    ),
    request_id: str | None = Query(default=None, max_length=64),
    from_time: datetime | None = Query(default=None),
    to_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_QUERY_OFFSET),
    include_total: bool = Query(default=False),
) -> EventListResponse:
    """Service-to-service чтение событий аудита.

    Контракт фильтров повторяет публичный `GET /events`, но аутентификация
    идёт по `SERVICE_API_KEYS` вместо user-bearer'а. Нормализуем `service` и
    `action` зеркально ingest'у — иначе запрос с raw-строкой (`AUTH_SERVICE`,
    confusable-юникод) не нашёл бы уже канонизированные в БД записи.
    """
    if service is not None:
        service = normalize_identifier(service)
    if action is not None:
        action = normalize_identifier(action)

    return event_service.query(
        db,
        department_id=department_id,
        service=service,
        severity=severity,
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        status=status_filter,
        request_id=request_id,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
        include_total=include_total,
        identity=None,
    )
