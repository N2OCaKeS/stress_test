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

Фильтр обязателен: caller должен передать и `service`, и `action`, чтобы
internal-канал не превращался в неограниченный дамп журнала под
service-key'ом. Если хотя бы один из них отсутствует — `422`
(`VALIDATION_ERROR`).

Read привязан к identity: `service` в запросе должен совпасть с
`X-Service-Identity` caller'а (тем же значением, что `require_service_token`
верифицировал по `SERVICE_API_KEYS`). Иначе держатель любого internal-ключа
читал бы аудит чужого сервиса — cross-service leak. Симметрично write-пути
(`SERVICE_IDENTITY_PAYLOAD_MISMATCH` на `POST /events`).
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.constants import Severity
from src.core.exceptions import AppException, DomainValidationError
from src.core.limiter import limiter
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


def _internal_read_rate_limit_key(request: Request) -> str:
    """Key-функция для rate-limit на `GET /internal/events`.

    Все internal caller'ы идут через один k8s ingress — per-IP bucket дал бы
    общий лимит на все сервисы, и один флудящий выжал бы бюджет остальных.
    Ключуемся по верифицированной `service_identity`, которую
    `require_service_token` положил в `request.state` после `compare_digest`.
    Fallback на IP — до аутентификации (когда state ещё не выставлен).
    """
    advertised = getattr(request.state, "service_identity", None)
    if advertised:
        return f"svc:{advertised}"
    return f"ip:{get_remote_address(request)}"


@router.get("/events", response_model=EventListResponse)
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=_internal_read_rate_limit_key,
)
def list_events_internal(
    request: Request,
    response: Response,
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
    """Service-to-service чтение СВОЕГО среза аудита.

    Контракт фильтров повторяет публичный `GET /events`, но аутентификация
    идёт по `SERVICE_API_KEYS` вместо user-bearer'а. `service` и `action`
    обязательны — без них internal-канал отдавал бы неограниченный дамп
    журнала под service-key'ом; отсутствие любого из них → `422`. Нормализуем
    их зеркально ingest'у — иначе запрос с raw-строкой (`AUTH_SERVICE`,
    confusable-юникод) не нашёл бы уже канонизированные в БД записи.

    `service` привязан к identity caller'а: нормализованное значение должно
    совпасть с верифицированным `X-Service-Identity`. Иначе держатель любого
    internal-ключа (их минимум 4 в `SERVICE_API_KEYS`) читал бы аудит другого
    сервиса — cross-service leak. Гард симметричен write-пути
    (`SERVICE_IDENTITY_PAYLOAD_MISMATCH`). `department_id` остаётся обычным
    фильтром: собственный срез сервиса легитимно охватывает его события во всех
    отделах (например `server_service` читает `server_account.drift_detected`
    по своим серверам из разных отделов).
    """
    if service is None or action is None:
        raise DomainValidationError(
            error_code="VALIDATION_ERROR",
            message="query parameters 'service' and 'action' are both required",
        )
    service = normalize_identifier(service)
    action = normalize_identifier(action)

    # Привязка к identity: caller читает только собственный срез. `require_service_token`
    # уже нормализовал identity перед stash'ем, `service` нормализован выше — сравнение
    # идёт в канонической форме, Unicode/confusable-обход закрыт.
    advertised = getattr(request.state, "service_identity", None)
    if advertised is not None and service != advertised:
        raise AppException(
            http_status=403,
            error_code="SERVICE_IDENTITY_QUERY_MISMATCH",
            message=(
                f"X-Service-Identity does not match query 'service' "
                f"(identity={advertised!r}, service={service!r}); a service-token "
                "caller may only read audit events for its own service"
            ),
        )

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
