"""Реестр сервисов и их action-каталогов.

POST /services/{service}/events — сервис регистрирует свои события
                                  (SERVICE_API_KEY).
GET  /services                  — read-роли смотрят список сервисов, когда-либо
                                  писавших события.
GET  /services/{service}/events — read-роли смотрят action'ы конкретного сервиса.
"""

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.constants import RESERVED_SERVICE_NAMES
from src.core.exceptions import AppException
from src.dependencies.auth import ReaderIdentity, require_service_token
from src.dependencies.db import get_db
from src.repositories import events as events_repo
from src.repositories import service_events as se_repo
from src.schemas.services import (
    RegisterEventsRequest,
    RegisterEventsResponse,
    ServiceEventsResponse,
    ServiceEventDetail,
    ServiceInfo,
    ServiceListResponse,
)
from src.utils.normalization import normalize_service_name

# Лимитер вынесен в `core.limiter` ради разрыва цикла import'ов
# (`src.main` собирает routers, эти routers импортили `src.main`).
# `app.state.limiter` указывает на тот же инстанс.
from src.core.limiter import limiter

router = APIRouter()


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
        "**Доступ:**\n"
        "- `loging_admin` / `account_admin` — видят все сервисы;\n"
        "- `loging_reader` / `department_admin` / service-роли в "
        "`loging_service` — видят только сервисы, писавшие события из их "
        "`department_id`. `event_count` / `last_event_at` агрегируются "
        "только по их отделу.\n\n"
        "**Связано:** `GET /services/{service}/events` — каталог action'ов "
        "конкретного сервиса; `GET /events` — собственно события."
    ),
)
def list_services(
    identity: ReaderIdentity,
    db: Session = Depends(get_db),
) -> ServiceListResponse:
    # Dept-scope enforcement: без фильтра dept-scoped reader
    # (loging_reader / department_admin / юзер с service-ролью в
    # loging_service) увидел бы `event_count` и `last_event_at` любого
    # сервиса, когда-либо писавшего из любого отдела — лик факта и
    # интенсивности cross-dept использования. Зеркалит `GET /events`,
    # который всегда форсит `_dept_scope`.
    #
    # `_dept_scope` выставляется в `require_reader`:
    #   * `loging_admin` / `account_admin` → `None` → unscoped (всё видно);
    #   * dept-scoped роли (`loging_reader` / `department_admin` / service-role
    #     в `loging_service`) → `<их department_id>` → repository агрегирует
    #     только по их отделу.
    dept_scope = identity.get("_dept_scope")
    rows = events_repo.list_services(db, department_id=dept_scope)
    items = [
        ServiceInfo(service=r.service, event_count=r.event_count, last_event_at=r.last_event_at)
        for r in rows
    ]
    return ServiceListResponse(items=items, total=len(items))


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
        429: {"description": "Превышен per-service-identity rate-limit"},
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
    service: str = Path(description="Имя сервиса, например 'auth_service'"),
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
    added, updated = se_repo.upsert_events(db, service, events_list)
    _, total = se_repo.list_for_service(db, service)
    return RegisterEventsResponse(service=service, added=added, updated=updated, total=total)


@router.get(
    "/{service}/events",
    response_model=ServiceEventsResponse,
    summary="Список зарегистрированных action'ов сервиса",
    description=(
        "Постранично отдаёт `service_events` конкретного сервиса: `action`, "
        "`description`, `default_severity`, `registered_at`, `updated_at`.\n\n"
        "**Доступ:** read-роли (`loging_admin` / `account_admin` / "
        "`loging_reader` / `department_admin` / service-роли в "
        "`loging_service`). Реестр сам по себе не содержит dept-зависимых "
        "данных, поэтому scope тут не применяется.\n\n"
        "**Связано:** `POST /services/{service}/events` — регистрация "
        "action'ов; `POST /rules` — правила на эти action'ы."
    ),
)
def list_service_events(
    identity: ReaderIdentity,
    service: str = Path(description="Имя сервиса, например 'auth_service'"),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
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
        limit=limit,
        offset=offset,
    )
