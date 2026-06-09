"""Эндпоинты приёма (ingest) и чтения событий аудита.

POST /events — пишут другие сервисы (SERVICE_API_KEY).
GET  /events — читают `loging_admin` / `loging_reader`. Обе роли видят журнал
              cross-dept целиком. `account_admin` / `department_admin` к
              чтению аудита НЕ допускаются (owner-decision): если dep_admin'у
              нужен read его отдела — выдать ему отдельную `loging_reader`.
"""

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.constants import RESERVED_SERVICE_NAMES, Severity
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError
from src.core.limits import MAX_QUERY_LIMIT, MAX_QUERY_OFFSET
from src.dependencies.auth import ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.schemas.common import ErrorEnvelope
from src.schemas.events import _IDEMPOTENCY_KEY_PATTERN, EventCreate, EventListResponse, EventResponse
from src.services import event_service
from src.utils.normalization import normalize_identifier

# Лимитер вынесен в `core.limiter` ради разрыва цикла import'ов
# (`src.main` собирает routers, эти routers импортили `src.main`).
# `app.state.limiter` указывает на тот же инстанс.
from src.core.limiter import limiter, reader_rate_limit_key

router = APIRouter()


def _idempotency_key_from_header(raw: str | None) -> str | None:
    """Нормализует header `Idempotency-Key` тем же путём, что и body-поле.

    Возвращает канонический key или None (если пустой / отсутствует). Невалидный
    charset после NFKC поднимает `DomainValidationError` → 422 — симметрично
    тому, как pydantic-валидатор `EventCreate._normalize_idempotency_key` отбил
    бы body-поле. Без этой симметрии header'ный path мог бы протащить байты,
    которые dedup-индекс не дедуплит (CR/LF/control).
    """
    if raw is None:
        return None
    normalised = unicodedata.normalize("NFKC", raw).strip()
    if not normalised:
        return None
    if not _IDEMPOTENCY_KEY_PATTERN.match(normalised):
        raise DomainValidationError(
            error_code="VALIDATION_ERROR",
            message=(
                "Idempotency-Key header must match [A-Za-z0-9_\\-.]{1,128} "
                "after NFKC normalisation (no CR/LF, no control chars)"
            ),
        )
    return normalised


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
        "если активное правило подавляет событие, возвращается 204 без тела (без content-length).\n\n"
        "**Доступ:** только service-to-service по `SERVICE_API_KEY` "
        "(`Authorization: Bearer <ключ>`). Пользовательский JWT не принимается.\n\n"
        "**Idempotency-Key:** ключ принимается из заголовка `Idempotency-Key` "
        "или из поля тела `idempotency_key` (legacy для обратной совместимости). "
        "Если заданы оба, значения должны совпасть после NFKC-нормализации — "
        "иначе 422 `VALIDATION_ERROR`. Дедуп идёт по паре `(service, idempotency_key)`.\n\n"
        "**Возможные ошибки:**\n"
        "- 401 `INVALID_SERVICE_KEY` — нет или неверный SERVICE_API_KEY.\n"
        "- 401 `MISSING_SERVICE_IDENTITY` — требуется header "
        "`X-Service-Identity` с известным значением "
        "(идентичность вне `SERVICE_API_KEYS` map'а отбивается тем же "
        "`INVALID_SERVICE_KEY`).\n"
        "- 403 `RESERVED_SERVICE_NAME` — попытка записать `service=loging_service` "
        "(зарезервировано для внутреннего self-audit, защита retention-инварианта).\n"
        "- 403 `SERVICE_IDENTITY_PAYLOAD_MISMATCH` — `X-Service-Identity` "
        "не совпадает с `payload.service`.\n"
        "- 409 `IDEMPOTENCY_KEY_CONFLICT` — `(service, idempotency_key)` уже использован "
        "с другим payload'ом.\n"
        "- 413 `PAYLOAD_TOO_LARGE` — body больше `MAX_REQUEST_BODY_BYTES`.\n"
        "- 400 `INVALID_CONTENT_LENGTH` — malformed `Content-Length` header.\n"
        "- 422 `VALIDATION_ERROR` — невалидный payload "
        "(глубина `details` > 10, NUL-байты, shadow-keys, кривой `request_id`, "
        "конфликт `Idempotency-Key` header'а с `idempotency_key` в теле).\n"
        "- 429 `RATE_LIMIT_EXCEEDED` — превышен лимит на сервис-идентичность "
        "(`X-Service-Identity`, fallback на IP; `INGEST_RATE_LIMIT`, по "
        "умолчанию 100/min).\n"
        "\n"
        "**Связано:** `POST /services/{service}/events` — регистрация каталога "
        "action'ов; `GET /events` — чтение записанных событий."
    ),
    response_description="Событие записано: id и `received_at` (UTC)",
    responses={
        204: {"description": "Событие подавлено правилом аудита (SUPPRESS, без body)"},
        400: {"model": ErrorEnvelope, "description": "`INVALID_CONTENT_LENGTH`"},
        401: {
            "model": ErrorEnvelope,
            "description": "`INVALID_SERVICE_KEY` / `MISSING_SERVICE_IDENTITY`",
        },
        403: {
            "model": ErrorEnvelope,
            "description": "`RESERVED_SERVICE_NAME` / `SERVICE_IDENTITY_PAYLOAD_MISMATCH`",
        },
        409: {"model": ErrorEnvelope, "description": "`IDEMPOTENCY_KEY_CONFLICT`"},
        413: {"model": ErrorEnvelope, "description": "`PAYLOAD_TOO_LARGE`"},
        422: {"model": ErrorEnvelope, "description": "`VALIDATION_ERROR` — см. `error_code` в envelope"},
        429: {"model": ErrorEnvelope, "description": "`RATE_LIMIT_EXCEEDED` (per-service-identity)"},
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
    lambda: get_settings().compose_ingest_rate_limit(),
    key_func=_ingest_rate_limit_key,
)
def create_event(
    request: Request,
    response: Response,
    payload: EventCreate,
    db: Session = Depends(get_db),
    idempotency_key_header: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Idempotency-Key (RFC draft / Stripe / GitHub convention). "
                "Совпадает по семантике с `payload.idempotency_key`. Если "
                "переданы оба, значения должны совпасть после NFKC."
            ),
            max_length=256,
        ),
    ] = None,
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

    # Cross-service idempotency poisoning: дедуп в `event_service.record` берёт
    # ключом пару `(payload.service, idempotency_key)`. Caller с валидным
    # SERVICE_API_KEY для `auth_service` мог послать payload с
    # `service="server_service"` и совпадающим `idempotency_key` — UNIQUE
    # отбил бы легитимный запрос второго сервиса, а в журнале остался бы
    # подделанный row. Гард симметричен `SERVICE_IDENTITY_PATH_MISMATCH` в
    # `POST /services/{service}/events`. Если identity не выставлен
    # (deployment без per-service keys) — пропускаем, остальной layered
    # auth уже отбивает.
    advertised = getattr(request.state, "service_identity", None)
    if advertised is not None and normalize_identifier(payload.service) != advertised:
        raise AuthorizationError(
            error_code="SERVICE_IDENTITY_PAYLOAD_MISMATCH",
            message=(
                f"X-Service-Identity does not match payload.service "
                f"(identity={advertised!r}, payload={payload.service!r}); "
                "a service-token caller may only ingest events under its own service"
            ),
        )

    # Idempotency-Key header (стандарт индустрии) сливается с
    # `payload.idempotency_key` (legacy для обратной совместимости — внутренние
    # сервисы дольше переезжают на header'ный путь). Header проходит через тот
    # же NFKC-нормализатор и charset-pattern, что и body-поле, через
    # `_idempotency_key_from_header()` helper. Конфликт (оба заданы и
    # различаются после нормализации) → 422: caller явно противоречит сам себе.
    header_normalised = _idempotency_key_from_header(idempotency_key_header)
    if header_normalised is not None:
        if payload.idempotency_key is None:
            payload.idempotency_key = header_normalised
        elif payload.idempotency_key != header_normalised:
            raise DomainValidationError(
                error_code="VALIDATION_ERROR",
                message=(
                    "Idempotency-Key header conflicts with body.idempotency_key "
                    "(values differ after NFKC normalisation)"
                ),
                details={
                    "header": header_normalised,
                    "body": payload.idempotency_key,
                },
            )

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
        "**Доступ:** `loging_admin` или `loging_reader` — обе роли видят "
        "журнал cross-dept. `account_admin` / `department_admin` к чтению "
        "audit'а не допускаются (если dep_admin'у нужно читать журнал — "
        "выдай ему отдельную `loging_reader`).\n\n"
        "**Фильтры** (любая комбинация, all-AND): `department_id`, `service`, "
        "`severity`, `action`, `actor_id`, `target_id`, `status`, `request_id`, "
        "`from_time`, `to_time`, `limit`, `offset`.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-user, fallback на IP "
        "если identity не определена; см. README).\n\n"
        "**Возможные ошибки:**\n"
        "- 401 `INVALID_TOKEN` / `MISSING_TOKEN` / `USER_BANNED` — нет или неверный JWT.\n"
        "- 403 `INSUFFICIENT_ROLE` — роль не в allow-list.\n"
        "- 429 `RATE_LIMIT_EXCEEDED` — превышен per-user лимит (fallback на IP).\n"
        "- 503 `AUTH_SERVICE_NOT_CONFIGURED` / `AUTH_SERVICE_TIMEOUT` / "
        "`AUTH_SERVICE_UNREACHABLE` / `AUTH_SERVICE_ERROR` / "
        "`INTROSPECT_KEY_NOT_CONFIGURED` / `INTROSPECT_NOT_INITIALIZED` — "
        "auth_service недоступен (introspect завалился).\n\n"
        "**Связано:** `POST /events` — приём событий; `GET /services` — реестр "
        "сервисов, когда-либо писавших события."
    ),
    response_description=(
        "Постранично: items + has_more + limit + offset. Поле total заполнено "
        "только при `include_total=true`, иначе null."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль/scope не подходят"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-user rate-limit"},
        503: {
            "model": ErrorEnvelope,
            "description": (
                "auth_service недоступен: `AUTH_SERVICE_NOT_CONFIGURED`, "
                "`AUTH_SERVICE_TIMEOUT`, `AUTH_SERVICE_UNREACHABLE`, "
                "`AUTH_SERVICE_ERROR`, `INTROSPECT_KEY_NOT_CONFIGURED`, "
                "`INTROSPECT_NOT_INITIALIZED`"
            ),
        },
    },
)
# Per-user лимит на read-канал: за k8s ingress все reader'ы приходят с
# одного IP, общий per-IP bucket позволил бы одному злоупотребляющему
# JWT/PAT'у выжать бюджет всех остальных операторов. Ключуемся по
# `sub` из introspect (см. `reader_rate_limit_key`); fallback на IP
# для запросов, где identity ещё не верифицирована (auth-fail —
# обычно отбивается до limit'а, но fallback закрывает гонку).
# slowapi требует `request: Request` параметром эндпоинта.
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def list_events(
    request: Request,
    response: Response,
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
    department_id: str | None = Query(default=None, description="Фильтр по ID отдела"),
    service: str | None = Query(default=None, description="Фильтр по имени сервиса-источника"),
    severity: Severity | None = Query(default=None),
    action: str | None = Query(default=None, description="Фильтр по имени action (точное совпадение)"),
    actor_id: str | None = Query(
        default=None,
        max_length=48,
        description="Фильтр по actor_id (точное совпадение)",
    ),
    target_id: str | None = Query(
        default=None,
        max_length=48,
        description="Фильтр по target_id (точное совпадение)",
    ),
    status_filter: Literal["success", "failure", "denied", "warning"] | None = Query(
        default=None,
        alias="status",
        description="Фильтр по исходу действия",
    ),
    request_id: str | None = Query(
        default=None,
        max_length=64,
        description="Фильтр по request_id (трассировочный идентификатор)",
    ),
    from_time: datetime | None = Query(default=None, description="Начало диапазона времени (ISO 8601)"),
    to_time: datetime | None = Query(default=None, description="Конец диапазона времени (ISO 8601)"),
    limit: int = Query(default=100, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_QUERY_OFFSET),
    include_total: bool = Query(
        default=False,
        description=(
            "Считать точное число событий под фильтр (COUNT по журналу). "
            "По умолчанию выключено — total не считается (поле `total` = null), "
            "а признак следующей страницы отдаётся через `has_more`."
        ),
    ),
) -> EventListResponse:
    # Dept-scope больше не применяется: `require_reader` пропускает только
    # `loging_admin` / `loging_reader`, обе роли — глобальные. Фильтр
    # `department_id` — обычный пользовательский query-параметр без
    # принудительного override'а.

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
        actor_id=actor_id,
        target_id=target_id,
        status=status_filter,
        request_id=request_id,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
        include_total=include_total,
        identity=identity,
    )
