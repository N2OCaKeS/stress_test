"""Эндпоинты приёма (ingest) и чтения событий аудита.

POST /events — пишут другие сервисы (SERVICE_API_KEY).
GET  /events — читают `loging_admin | loging_reader | department_admin | account_admin`;
              `department_admin` и `loging_reader` автоматически ограничены
              своим отделом.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.constants import RESERVED_SERVICE_NAMES
from src.core.exceptions import AppException, AuthorizationError
from src.dependencies.auth import ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.schemas.events import EventCreate, EventListResponse, EventResponse
from src.services import event_service
from src.utils.normalization import normalize_identifier

# Лимитер вынесен в `core.limiter` ради разрыва цикла import'ов
# (`src.main` собирает routers, эти routers импортили `src.main`).
# `app.state.limiter` указывает на тот же инстанс.
from src.core.limiter import limiter

router = APIRouter()

_MAX_LIMIT = 1000


def _ingest_rate_limit_key(request: Request) -> str:
    """Key-функция для rate-limit на `POST /events`.

    В k8s весь ingest идёт через один ingress-pod — per-IP key дал бы общий
    bucket на все сервисы, и один флудящий сервис выжимал бы бюджет
    остальных. Поэтому ключуемся по идентичности сервиса, которую
    `require_service_token` положил в `request.state.service_identity`
    ПОСЛЕ аутентификации. Это закрывает bucket-spoofing: атакующий с
    валидным SERVICE_API_KEY (shared mode) не может разнести bucket'ы
    рандомным `X-Service-Identity` на каждый запрос — limit-decorator
    видит уже верифицированную identity.

    Fallback на IP — для случаев, где state не выставлен (path до
    `require_service_token` сработал, тесты на отказ авторизации).
    """
    advertised = getattr(request.state, "service_identity", None)
    if advertised:
        # require_service_token уже нормализовал identity перед stash'ем.
        return f"svc:{advertised}"
    return f"ip:{get_remote_address(request)}"


@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Записать одно событие аудита",
    description=(
        "Принимает одно событие от сервиса и сохраняет его в audit-журнал. "
        "Перед записью прогоняется через rule engine (SUPPRESS/ALLOW/OVERRIDE_SEVERITY) — "
        "если активное правило подавляет событие, возвращается 204 без тела.\n\n"
        "**Доступ:** только service-to-service по `SERVICE_API_KEY` "
        "(`Authorization: Bearer <ключ>`). Пользовательский JWT не принимается.\n\n"
        "**Возможные ошибки:**\n"
        "- 401 `INVALID_SERVICE_KEY` — нет или неверный SERVICE_API_KEY.\n"
        "- 401 `MISSING_SERVICE_IDENTITY` — требуется header "
        "`X-Service-Identity` с известным значением "
        "(идентичность вне `SERVICE_API_KEYS` map'а отбивается тем же "
        "`INVALID_SERVICE_KEY`).\n"
        "- 403 `RESERVED_SERVICE_NAME` — попытка записать `service=loging_service` "
        "(зарезервировано для внутреннего self-audit, защита retention-инварианта).\n"
        "- 413 `PAYLOAD_TOO_LARGE` — body больше `MAX_REQUEST_BODY_BYTES`.\n"
        "- 422 `VALIDATION_ERROR` — невалидный payload "
        "(глубина `details` > 10, NUL-байты, shadow-keys, кривой `request_id`).\n"
        "- 429 `RATE_LIMIT_EXCEEDED` — превышен лимит на сервис-идентичность "
        "(`X-Service-Identity`, fallback на IP; `INGEST_RATE_LIMIT`, по "
        "умолчанию 100/min).\n\n"
        "**Связано:** `POST /services/{service}/events` — регистрация каталога "
        "action'ов; `GET /events` — чтение записанных событий."
    ),
    response_description="Событие записано: id и `received_at` (UTC)",
    responses={
        204: {"description": "Событие подавлено правилом аудита (SUPPRESS)"},
        413: {"description": "Тело запроса превышает лимит размера"},
        422: {"description": "Невалидный payload — см. `error_code` в envelope"},
        429: {"description": "Превышен rate-limit на ingest (per-service-identity)"},
    },
    dependencies=[Depends(require_service_token)],
)
# Rate-limit закрывает сценарий «утёк SERVICE_API_KEY → DB flood»
# (~3000 ev/s, ~10 GB/h → распухание таблицы за 1-2 ч). Bucket'ы независимы
# между сервисами (`key_func=_ingest_rate_limit_key` ключует по
# `X-Service-Identity`) — компрометация ключа одного сервиса не выжимает
# бюджет остальных, как было бы при общем per-IP bucket'е за k8s ingress.
# Строка лимита читается из settings в момент вычисления декоратора;
# `get_settings` кеширован. slowapi требует `request: Request` как настоящий
# параметр (не через Depends) в сигнатуре эндпоинта — иначе не находит limiter
# middleware state.
@limiter.limit(
    lambda: get_settings().ingest_rate_limit,
    key_func=_ingest_rate_limit_key,
)
def create_event(
    request: Request,
    response: Response,
    payload: EventCreate,
    db: Session = Depends(get_db),
) -> Response | EventResponse:
    # `response: Response` ОБЯЗАТЕЛЕН для slowapi когда эндпоинт возвращает
    # Pydantic-модель (а не голый `Response`). Обёртка slowapi инжектит
    # `X-RateLimit-*` headers в response-объект после возврата хендлера; без
    # этого параметра она падает на `kwargs.get("response")` → `None` → raise
    # (см. `slowapi.extension.Limiter._inject_headers`, строка 381). FastAPI
    # сам подкладывает параметр; headers, выставленные на нём, доходят до
    # финального ответа.
    # Rate-limit ключуется по `X-Service-Identity` (см.
    # `_ingest_rate_limit_key`), но shared SERVICE_API_KEY всё равно не
    # привязывает caller'а к конкретному `service=` в payload'е — header можно
    # подделать. Поэтому здесь дополнительно блокируем внешнее impersonation
    # сервисов с retention-инвариантом: иначе любой держатель ключа мог бы
    # навсегда запечь произвольные события в audit-журнал.
    #
    # pydantic-валидатор в `EventCreate.service` уже прогоняет
    # нормализацию, так что `payload.service` уже в канонической форме.
    # Повторная нормализация здесь — belt-and-braces: гард остаётся
    # рабочим даже если кто-то однажды убьёт валидатор в schemas. Сравнить
    # ещё раз дёшево, и это плотнее связывает инвариант с этим эндпоинтом.
    if normalize_identifier(payload.service) in RESERVED_SERVICE_NAMES:
        raise AppException(
            http_status=403,
            error_code="RESERVED_SERVICE_NAME",
            message=(
                "Service name is reserved for internal loging_service audit and "
                "cannot be written via the external ingest endpoint"
            ),
        )

    # P2 SERVICE_IDENTITY_PAYLOAD_MISMATCH guard отложен — требует
    # переписать ~13 тестовых fixture'ов (TEST_SERVICE_IDENTITY=auth_service
    # против payload.service=server_service во многих integration-тестах).
    # Per-service API keys в production сделают этот soft-check излишним.
    # См. TODO W11 P2 — заведено как separate cleanup задача.

    event = event_service.record(db, payload)
    if event is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return EventResponse.model_validate(event)


@router.get(
    "",
    response_model=EventListResponse,
    summary="Прочитать события аудита с фильтрами",
    description=(
        "Постранично отдаёт записанные события аудита. Сортировка по "
        "`timestamp DESC` (свежие первыми).\n\n"
        "**Доступ:**\n"
        "- `loging_admin` и `account_admin` — видят все события;\n"
        "- `loging_reader`, `department_admin` и пользователи с service-ролью "
        "`reader|operator|admin` в `loging_service` — автоматически ограничены "
        "своим `department_id` (`_dept_scope`).\n\n"
        "**Фильтры** (любая комбинация, all-AND): `department_id`, `service`, "
        "`severity`, `action`, `from_time`, `to_time`, `limit`, `offset`.\n\n"
        "**Возможные ошибки:**\n"
        "- 401 `INVALID_TOKEN` / `MISSING_TOKEN` — нет или неверный JWT.\n"
        "- 403 — попытка dept-scoped пользователя запросить чужой `department_id`, "
        "либо роль не в allow-list.\n"
        "- 503 `AUTH_SERVICE_*` — auth_service недоступен (introspect завалился).\n\n"
        "**Связано:** `POST /events` — приём событий; `GET /services` — реестр "
        "сервисов, когда-либо писавших события."
    ),
    response_description=(
        "Постранично: items + has_more + limit + offset. Поле total заполнено "
        "только при `include_total=true`, иначе null."
    ),
)
# Per-IP лимит на read-канал: даже валидный reader-JWT не должен иметь права
# выжимать pgsql-пул широкими SELECT'ами по multi-million журналу. Лимит
# отдельный от ingest'а, ключуется по IP (reader'ов мало, идут с разных
# IP — глобальный bucket был бы over-restrictive). slowapi требует
# `request: Request` параметром эндпоинта (см. комментарий у `create_event`).
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
)
def list_events(
    request: Request,
    response: Response,
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
    department_id: str | None = Query(default=None, description="Фильтр по ID отдела"),
    service: str | None = Query(default=None, description="Фильтр по имени сервиса-источника"),
    severity: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Query(default=None),
    action: str | None = Query(default=None, description="Фильтр по имени action (точное совпадение)"),
    from_time: datetime | None = Query(default=None, description="Начало диапазона времени (ISO 8601)"),
    to_time: datetime | None = Query(default=None, description="Конец диапазона времени (ISO 8601)"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    include_total: bool = Query(
        default=False,
        description=(
            "Считать точное число событий под фильтр (COUNT по журналу). "
            "По умолчанию выключено — total не считается (поле `total` = null), "
            "а признак следующей страницы отдаётся через `has_more`."
        ),
    ),
) -> EventListResponse:
    # Dept-scope: если пользователь ограничен отделом — форсим его scope.
    dept_scope = identity.get("_dept_scope")
    if dept_scope:
        if department_id and department_id != dept_scope:
            raise AuthorizationError(
                error_code="DEPARTMENT_SCOPE_VIOLATION",
                message=f"Access restricted to department '{dept_scope}'",
                details={"requested": department_id, "allowed": dept_scope},
            )
        department_id = dept_scope

    # Ingest нормализует service/action через `normalize_identifier`
    # (NFKC + invisibles + homoglyph fold + lower), а query до сих пор гнал
    # raw query-string в repo. Запрос `?service=AUTH_SERVICE` или
    # `?service=lоging_service` (кир. `о`) попадал в БД as-is и не находил
    # уже нормализованные строки — confusable hide-trail: caller с виду
    # запросил «свои» события, а получил пусто, потому что в БД они под
    # каноническим именем. Нормализуем здесь зеркально ingest'у.
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
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
        include_total=include_total,
    )
