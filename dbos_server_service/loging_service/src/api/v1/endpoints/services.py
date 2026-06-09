"""Реестр сервисов и их action-каталогов.

POST /services/{service}/events — сервис регистрирует свои события
                                  (SERVICE_API_KEY).
GET  /services                  — read-роли смотрят список сервисов, когда-либо
                                  писавших события.
GET  /services/{service}/events — read-роли смотрят action'ы конкретного сервиса.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.constants import RESERVED_SERVICE_NAMES
from src.core.exceptions import AppException
from src.core.limits import MAX_QUERY_LIMIT, MAX_QUERY_OFFSET
from src.dependencies.auth import ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.repositories import events as events_repo
from src.repositories import service_events as se_repo
from src.schemas.common import ErrorEnvelope
from src.schemas.events import EventCreate
from src.schemas.services import (
    RegisterEventsRequest,
    RegisterEventsResponse,
    ServiceEventsResponse,
    ServiceEventDetail,
    ServiceInfo,
    ServiceListResponse,
)
from src.services import event_service
from src.utils.normalization import normalize_service_name

# Лимитер вынесен в `core.limiter` ради разрыва цикла import'ов
# (`src.main` собирает routers, эти routers импортили `src.main`).
# `app.state.limiter` указывает на тот же инстанс.
from src.core.limiter import limiter, reader_rate_limit_key

router = APIRouter()

# Charset для path-параметра `service`: snake_case ASCII после NFKC
# (тот же regex, что у `EventCreate.service` в schemas/events.py). Bounded
# input снимает с NFKC'ка `normalize_service_name` нагрузку от unbounded
# string и публикует charset/length в OpenAPI.
_SERVICE_PATH_PATTERN = r"^[^/\s]{1,128}$"


def _register_events_rate_limit_key(request: Request) -> str:
    """Key-функция для rate-limit на `POST /services/{service}/events`.

    Мы НЕ можем key'ить per-IP здесь, потому что batch-канал доступен только
    internal caller'ам через k8s ingress — все request приходят с одинаковым
    IP-адресом nginx'а. Per-IP было бы либо too loose (общий bucket на все
    внутренние сервисы), либо самобан легитимного трафика.

    Стратегия: берём верифицированную identity из `request.state`
    (`require_service_token` положил её туда после `compare_digest` против
    `SERVICE_API_KEYS[identity]`). Bucket'ы независимы между сервисами:
    compromised SERVICE_API_KEY заявляющий `auth_service` не выжмет бюджет
    `server_service`. Trust'а unauthenticated header'а здесь нет —
    атакующий не может разносить bucket'ы spoof'ом X-Service-Identity.

    Fallback на IP — если auth ещё не отработал (нет state); в legacy
    single-key deploy без X-Service-Identity бакеты сольются в один IP-key.
    """
    advertised = getattr(request.state, "service_identity", None)
    if advertised:
        return f"svc:{advertised}"
    return f"ip:{get_remote_address(request)}"

# `RESERVED_SERVICE_NAMES` живёт в `core/constants.py`. Сравнение через
# `normalize_service_name` симметрично с `POST /events` ingest-гардом и
# отбивает casing/ZWSP-padding/confusables bypass'ы.


@router.get(
    "",
    response_model=ServiceListResponse,
    summary="Список сервисов, когда-либо писавших события аудита",
    description=(
        "Возвращает агрегат: `service`, `event_count`, `last_event_at` по каждому "
        "сервису, у которого есть хотя бы одно событие в `audit_events`.\n\n"
        "Список заведомо короткий (десятки сервисов на платформе), поэтому "
        "пагинации нет: `has_more=False`, `limit`/`offset` всегда `null`, "
        "`total = len(items)`.\n\n"
        "**Доступ:** `loging_admin` или `loging_reader` — обе роли видят "
        "агрегат cross-dept целиком. `account_admin` / `department_admin` "
        "к чтению audit'а не допускаются.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-IP, см. README).\n\n"
        "**Связано:** `GET /services/{service}/events` — каталог action'ов "
        "конкретного сервиса; `GET /events` — собственно события."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "`INSUFFICIENT_ROLE`"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-IP rate-limit"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
# Per-user лимит на read-канал реестра сервисов. Симметрия с `GET /events`:
# даже валидный reader-JWT не должен иметь права burst'ом выжимать pgsql-пул
# через group-by COUNT(*) агрегат по миллионному журналу. За ingress общий
# per-IP bucket позволил бы одному JWT выжать бюджет других reader'ов.
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def list_services(
    request: Request,
    response: Response,
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
) -> ServiceListResponse:
    # Read-роли (`loging_admin` / `loging_reader`) обе глобальные — агрегат
    # GROUP BY service отдаётся cross-dept без фильтра. Историческая
    # dept-scoped семантика для `loging_reader` / `department_admin` снята:
    # `department_admin` к loging_service не допускается, а `loging_reader`
    # теперь global-read (см. `dependencies/auth.py::require_reader`).
    rows = events_repo.list_services(db)
    items = [
        ServiceInfo(service=r.service, event_count=r.event_count, last_event_at=r.last_event_at)
        for r in rows
    ]
    # Список заведомо короткий — реальной пагинации тут нет, но shape выровнен
    # с EventListResponse / RuleListResponse / ServiceEventsResponse, чтобы
    # клиенты не дёргали поля по-разному. `has_more=False` всегда: вернули всё,
    # что есть; `limit`/`offset` None — параметров запрос не принимал.
    return ServiceListResponse(
        items=items,
        total=len(items),
        has_more=False,
        limit=None,
        offset=None,
    )


@router.post(
    "/{service}/events",
    response_model=RegisterEventsResponse,
    status_code=status.HTTP_200_OK,
    summary="Зарегистрировать (upsert) список событий сервиса",
    description=(
        "Idempotent upsert action-каталога конкретного сервиса. Принимает "
        "полный список событий, которые сервис умеет эмитить. Существующие "
        "action'ы обновляются (description / default_severity), новые — "
        "добавляются. Возвращает счётчики `added` / `updated` / `total`.\n\n"
        "**Доступ:** только service-to-service по `SERVICE_API_KEY`.\n"
        "Если выставлен header `X-Service-Identity` — он должен совпадать с "
        "`{service}` в URL: caller с service-token'ом может регистрировать "
        "события только под собственным именем (защита от cross-tenant "
        "audit-trail poisoning).\n\n"
        "**Возможные ошибки:**\n"
        "- 401 `INVALID_SERVICE_KEY` — нет или неверный SERVICE_API_KEY.\n"
        "- 403 `RESERVED_SERVICE_NAME` — попытка зарегистрировать события "
        "под `loging_service` (зарезервировано для self-audit).\n"
        "- 403 `SERVICE_IDENTITY_PATH_MISMATCH` — `X-Service-Identity` "
        "не совпадает с `{service}` из URL.\n"
        "- 429 `RATE_LIMIT_EXCEEDED` — превышен per-service-identity лимит "
        "(`REGISTER_EVENTS_RATE_LIMIT`, по умолчанию 100/min).\n\n"
        "**Связано:** `GET /services/{service}/events` — посмотреть, что "
        "зарегистрировано; `POST /rules` — создание SUPPRESS/ALLOW-правил "
        "на зарегистрированные action'ы."
    ),
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "`INVALID_SERVICE_KEY` / `MISSING_SERVICE_IDENTITY`",
        },
        403: {
            "model": ErrorEnvelope,
            "description": "`RESERVED_SERVICE_NAME` / `SERVICE_IDENTITY_PATH_MISMATCH`",
        },
        413: {"model": ErrorEnvelope, "description": "`PAYLOAD_TOO_LARGE`"},
        422: {"model": ErrorEnvelope, "description": "`VALIDATION_ERROR`"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-service-identity rate-limit"},
    },
    dependencies=[Depends(require_service_token)],
)
# Per-service-identity rate-limit. Симметрия с `POST /events`
# (см. `endpoints/events.py::create_event`), но bucket key'ится по
# `X-Service-Identity`, а не IP — все internal caller'ы идут через один
# k8s ingress nginx, per-IP был бы либо too loose, либо самобаном легита.
# Без этого SERVICE_API_KEY'оноситель дудосит ingest через bulk-POST
# (`register_events` принимает каталог из 1000 action'ов одним запросом).
# Строка лимита читается из settings во
# время execution лямбды, `get_settings` кеширован.
@limiter.limit(
    lambda: get_settings().register_events_rate_limit,
    key_func=_register_events_rate_limit_key,
)
def register_events(
    request: Request,
    response: Response,
    service: str = Path(
        min_length=1,
        max_length=64,
        pattern=_SERVICE_PATH_PATTERN,
        description="Имя сервиса, например 'auth_service'",
    ),
    payload: RegisterEventsRequest = ...,
    db: Session = Depends(get_db),
) -> RegisterEventsResponse:
    # Нормализуем path-параметр на входе: тот же NFKC+invisibles+confusables+
    # lower, что и `EventCreate._normalize_service` на ingest-пути. Без этого
    # catalog (`service_events.service`) пишется raw, а `audit_events.service`
    # уже нормализован — две таблицы расходятся: `GET /services/{svc}/events`
    # ищет по raw-имени, тогда как `GET /events?service=...` по нормализованному.
    # Все downstream проверки и repo-вызовы используют именно `service` (после
    # переприсваивания), чтобы reserved-guard, identity-match и upsert работали
    # с одним и тем же значением.
    service = normalize_service_name(service)

    # Per-service идентификация пока не реализована: shared
    # SERVICE_API_KEY не привязывает caller'а к конкретному `service` в пути.
    # До этого момента блокируем внешнее impersonation сервисов, чей
    # action-каталог принадлежит loging_service'у внутренне — иначе любой
    # держатель ключа мог бы залить fake-action'ы под `loging_service` и
    # подогнать SUPPRESS-правила под собственный аудит.
    # Зеркалит гард на `POST /events` (см. `events.py`): сравнение через
    # `normalize_service_name` (NFKC + invisibles strip + homoglyph fold +
    # lower), так что Unicode-bypass'ы (кириллица `о`, ZWSP-padding, …)
    # тоже отбиваются.
    if service in RESERVED_SERVICE_NAMES:
        raise AppException(
            http_status=403,
            error_code="RESERVED_SERVICE_NAME",
            message=(
                "Service name is reserved for internal loging_service audit and "
                "cannot be registered via the external service-token endpoint"
            ),
        )

    # ── Path-vs-identity гард ─────────────────────────────────────────────
    # Если caller прислал `X-Service-Identity` (внутренние сервисы это
    # делают) — path-параметр должен совпасть. Без этого держатель shared
    # SERVICE_API_KEY, представляясь `auth_service`, мог бы переписать
    # event-каталог `server_service` — cross-tenant audit-trail poisoning.
    # Per-service API keys сделают soft-check излишним.
    # Если header отсутствует — пропускаем (soft mode warn'ит в
    # `require_service_token`; strict mode кидает 401
    # ещё в зависимости).
    advertised = getattr(request.state, "service_identity", None)
    if advertised is not None and normalize_service_name(advertised) != service:
        raise AppException(
            http_status=403,
            error_code="SERVICE_IDENTITY_PATH_MISMATCH",
            message=(
                "X-Service-Identity does not match path "
                f"(identity={advertised!r}, path={service!r}); a service-token "
                "caller may only register events for its own service"
            ),
        )

    events_list = [ev.model_dump() for ev in payload.events]
    added, updated = se_repo.upsert_events(db, service, events_list, commit=False)
    # Сбрасываем catalog-severity кеш сразу после upsert'а — без этого
    # свежий `default_severity` подхватится только через TTL (до 30 сек
    # lag в hot-ingest, заметно в тестах и в demo-флоу).
    from src.services.rule_service import invalidate_catalog_severity_cache
    invalidate_catalog_severity_cache()
    # Голый COUNT(*) вместо `list_for_service(..., limit=1000)`: total нужен
    # только для audit-details, материализация row'ей — впустую. На сервисе с
    # >1000 зарегистрированных action'ов прежний код ещё и врал бы (capped).
    total = se_repo.count_for_service(db, service)

    # Self-audit: registry-mutation идёт от service-token caller'а, так что
    # actor_type=service, actor_id=верифицированная X-Service-Identity (или
    # ── на legacy soft-mode без header'а ── имя сервиса из path). Без audit
    # admin не увидит, кто и когда переписал каталог action'ов (rules/retention
    # writes аудитируются — здесь была дыра в симметрии). Идём через ту же
    # tx, что и upsert (`commit=False` выше); единственный commit ниже.
    # `advertised` уже прочитан выше для path-vs-identity гарда — используем то же
    # значение, чтобы две ветки гарантированно совпадали и одно атомарное чтение.
    # `service` path-param разрешает до 64 символов (snake_case + цифры по
    # _SERVICE_PATTERN), а `EventCreate.actor_id` / `target_id` ограничены
    # 48 — длинное service-имя без trim'а валило бы self-audit с 422.
    # Truncate'аем детерминированно: легитимный actor совпадает с identity
    # на префиксе, downstream-аналитика не теряет атрибуцию.
    actor_id = (advertised or service)[:48]
    target_id_trunc = service[:48]
    event_service.record_admin_action(
        db,
        EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.service_events_registered",
            actor_id=actor_id,
            actor_type="service",
            target_id=target_id_trunc,
            target_type="service_event",
            status="success",
            allowed=True,
            severity=None,
            details={
                "service": service,
                "added": added,
                "updated": updated,
                "total": total,
            },
        ),
        commit=False,
    )
    db.commit()
    return RegisterEventsResponse(service=service, added=added, updated=updated, total=total)


@router.get(
    "/{service}/events",
    response_model=ServiceEventsResponse,
    summary="Список зарегистрированных action'ов сервиса",
    description=(
        "Постранично отдаёт `service_events` конкретного сервиса: `action`, "
        "`description`, `default_severity`, `registered_at`, `updated_at`.\n\n"
        "**Доступ:** `loging_admin` или `loging_reader`. `account_admin` / "
        "`department_admin` к чтению реестра не допускаются.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-IP, см. README).\n\n"
        "**Связано:** `POST /services/{service}/events` — регистрация "
        "action'ов; `POST /rules` — правила на эти action'ы."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "`INSUFFICIENT_ROLE`"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-IP rate-limit"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def list_service_events(
    request: Request,
    response: Response,
    identity: ReaderIdentity,
    service: str = Path(
        min_length=1,
        max_length=64,
        pattern=_SERVICE_PATH_PATTERN,
        description="Имя сервиса, например 'auth_service'",
    ),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_QUERY_OFFSET),
) -> ServiceEventsResponse:
    # Симметрия с register_events: catalog хранится с нормализованным именем,
    # query по raw path-параметру не нашёл бы row, зарегистрированную через
    # `AUTH_SERVICE` / `lоging_service` / `auth_service​`.
    service = normalize_service_name(service)
    rows, total = se_repo.list_for_service(db, service, limit=limit, offset=offset)
    return ServiceEventsResponse(
        service=service,
        items=[ServiceEventDetail.model_validate(r) for r in rows],
        total=total,
        has_more=offset + len(rows) < total,
        limit=limit,
        offset=offset,
    )
