# loging_service API endpoints

> **Версия сервиса:** `0.1.0` (`pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/logging/v1`.
> **Источники аудита:** см. `AUDIT_EVENTS.md`.

Документ описывает публичный HTTP-контракт `loging_service`: список endpoint'ов с auth-режимом, ответами и стабильными `error_code`. Каталог автоматически не генерируется; правки делаем вручную при изменении кода.

## Общие правила

- Все эндпоинты под `/api/logging/v1`. В prod доступны только по HTTPS (TLS-guard middleware).
- Service-to-service ingest (`POST /events`, `POST /services/{service}/events`) защищён `SERVICE_API_KEYS` map + обязательный `X-Service-Identity` заголовок.
- Read-эндпоинты (`GET /events`, `GET /services`, `GET /services/{service}/events`, `GET /rules`, `GET /rules/{rule_id}`, `GET /retention`) идут по user JWT — introspect в `auth_service`. Допускаются ТОЛЬКО `loging_admin` / `loging_reader`. `account_admin` / `department_admin` к чтению audit'а не имеют доступа: если dep_admin'у нужен read его отдела, ему выдаётся отдельная `loging_reader`.
- Write на `/rules` и `/retention` доступен только `loging_admin`. `account_admin` / `department_admin` к управлению loging_service не допускаются.
- Все значимые действия публикуются как self-audit (`logging.*` actions, см. `AUDIT_EVENTS.md`).
- Эндпоинты `/health`, `/ready`, `/token` исключены из OpenAPI (`include_in_schema=False`).

### Формат ошибки (envelope)

```json
{
  "error": "forbidden",
  "error_code": "RESERVED_SERVICE_NAME",
  "message": "Service name is reserved for internal loging_service audit and cannot be written via the external ingest endpoint",
  "details": {},
  "request_id": "req_abc",
  "timestamp": "2026-06-05T10:00:00Z"
}
```

Схема — `src/schemas/common.py::ErrorEnvelope`.

### Заголовки ответа

- `X-Request-ID` — корелляционный ID (генерируется middleware если не передан, charset `[A-Za-z0-9_.-]{1,64}`).
- `Retry-After` — на 429.
- `X-Export-Truncated` — `true`/`false` на `GET /events/export`: `true`, если под фильтр попало больше `MAX_EXPORT_ROWS` строк и ответ усечён.
- `Content-Disposition` — `attachment; filename="audit-export-<from>-<to>.csv"` на `GET /events/export`.
- Security headers (HSTS опционально, X-Frame-Options, CSP, Permissions-Policy) проставляет `SecurityHeadersMiddleware`.

### Пагинация

Все list-эндпоинты используют `limit`/`offset`. Конкретный response shape различается (см. таблицу ниже) — на больших журналах `GET /events` дополнительно отдаёт `has_more`, а `total` считается только при `?include_total=true`.

### Rate-limit

| Группа | ENV-переменная | Default | Применяется на |
|---|---|---|---|
| Ingest | `INGEST_RATE_LIMIT` | `100/minute` per X-Service-Identity | `POST /events` |
| Register events | `REGISTER_EVENTS_RATE_LIMIT` | `100/minute` per X-Service-Identity | `POST /services/{service}/events` |
| Audit query | `AUDIT_QUERY_RATE_LIMIT` | `100/minute` per user (`sub` из introspect, fallback на IP) | `GET /events`, `GET /events/stats`, `GET /events/export`, `GET /rules`, `GET /rules/{id}`, `GET /services`, `GET /services/{service}/events`, `GET /retention` |

Write-эндпоинты `/rules` (POST/PATCH/DELETE) и `/retention` (PUT/DELETE) **без** rate-limit — by-design: admin-операции выполняются вручную, утечка admin-токена детектится не RL'ом, а audit-каналом.

---

## Catalog `error_code`

Сгруппированы по причине. HTTP-статус указан рядом.

### Ingest / service-token

| `error_code` | HTTP | Источник |
|---|---|---|
| `INVALID_SERVICE_KEY` | 401 | Нет/неверный SERVICE_API_KEY, identity вне `SERVICE_API_KEYS`, или `SERVICE_API_KEYS` пуст (deployment misconfig) |
| `MISSING_SERVICE_IDENTITY` | 401 | Не задан заголовок `X-Service-Identity` |
| `RESERVED_SERVICE_NAME` | 403 | Попытка писать события под `loging_service` |
| `SERVICE_IDENTITY_PAYLOAD_MISMATCH` | 403 | `X-Service-Identity` ≠ `payload.service` (`POST /events`) |
| `SERVICE_IDENTITY_PATH_MISMATCH` | 403 | `X-Service-Identity` ≠ `{service}` в URL (`POST /services/{service}/events`) |

### Authentication (user-JWT / PAT / bot)

| `error_code` | HTTP | Источник |
|---|---|---|
| `MISSING_TOKEN` | 401 | Не задан Authorization-заголовок |
| `INVALID_TOKEN` | 401 | introspect вернул `active=false` |
| `USER_BANNED` | 401 | introspect вернул `is_banned=true` |
| `INVALID_CREDENTIALS` | 401 | `POST /token` proxy: auth_service ответил 4xx/5xx |

### Authorization

| `error_code` | HTTP | Источник |
|---|---|---|
| `INSUFFICIENT_ROLE` | 403 | `require_admin` (нужен `loging_admin`) / `require_reader` (нужен `loging_admin` или `loging_reader`): роль не подходит |

### Upstream (auth_service)

| `error_code` | HTTP | Источник |
|---|---|---|
| `AUTH_SERVICE_NOT_CONFIGURED` | 503 | `AUTH_SERVICE_URL` пуст |
| `AUTH_SERVICE_TIMEOUT` | 503 | introspect не ответил за timeout |
| `AUTH_SERVICE_UNREACHABLE` | 503 | connect refused/network error |
| `AUTH_SERVICE_ERROR` | 503 | non-200 от introspect или транспортный exception |
| `INTROSPECT_KEY_NOT_CONFIGURED` | 503 | `INTROSPECT_SERVICE_API_KEY` пуст |
| `INTROSPECT_NOT_INITIALIZED` | 503 | Pooled `_introspect_client` не поднят (lifespan не запускался) |

### Rules / retention domain

| `error_code` | HTTP | Источник |
|---|---|---|
| `RULE_NOT_FOUND` | 404 | `GET/PATCH/DELETE /rules/{rule_id}` — записи нет |
| `RULE_NAME_CONFLICT` | 409 | UNIQUE на `audit_rules.name` |
| `EFFECT_SEVERITY_REQUIRED` | 422 | `effect=OVERRIDE_SEVERITY` без `effect_severity` |
| `EFFECT_SEVERITY_NOT_ALLOWED` | 422 | `effect ≠ OVERRIDE_SEVERITY`, но `effect_severity` задан |
| `UNKNOWN_MATCH_ACTION` | 422 | `match_action` (точное) не зарегистрирован в реестре |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | `(service, idempotency_key)` совпал, payload отличается |

### Middleware / infra

| `error_code` | HTTP | Источник |
|---|---|---|
| `INVALID_CONTENT_LENGTH` | 400 | Body-size middleware: `Content-Length` malformed |
| `PAYLOAD_TOO_LARGE` | 413 | Body больше `MAX_REQUEST_BODY_BYTES` |
| `VALIDATION_ERROR` | 422 | Pydantic-обёртка `RequestValidationError` |
| `RATE_LIMIT_EXCEEDED` | 429 | slowapi: per-key bucket исчерпан |
| `INTERNAL_ERROR` | 500 | IntegrityError, не подходящий под UNIQUE-классификатор; иные generic 500 |
| `INVALID_REQUEST_ID` | 422 | `EventCreate.request_id` не прошёл charset |
| `ADMIN_AUDIT_WRONG_SERVICE` | 422 | `record_admin_action` вызван с `service ≠ loging_service` |

---

## Endpoints

### Ingest

| Method | URL | Auth | Body | Response | Возможные `error_code` |
|---|---|---|---|---|---|
| POST | `/events` | service-token | `EventCreate` | 201 `EventResponse`; 204 при SUPPRESS | `INVALID_SERVICE_KEY`, `MISSING_SERVICE_IDENTITY`, `RESERVED_SERVICE_NAME`, `SERVICE_IDENTITY_PAYLOAD_MISMATCH`, `PAYLOAD_TOO_LARGE`, `INVALID_CONTENT_LENGTH`, `VALIDATION_ERROR`, `RATE_LIMIT_EXCEEDED`, `IDEMPOTENCY_KEY_CONFLICT` |
| POST | `/services/{service}/events` | service-token | `RegisterEventsRequest` | 200 `RegisterEventsResponse` | `INVALID_SERVICE_KEY`, `MISSING_SERVICE_IDENTITY`, `RESERVED_SERVICE_NAME`, `SERVICE_IDENTITY_PATH_MISMATCH`, `PAYLOAD_TOO_LARGE`, `VALIDATION_ERROR`, `RATE_LIMIT_EXCEEDED` |

### Read events / services

| Method | URL | Auth | Response | Возможные `error_code` |
|---|---|---|---|---|
| GET | `/events` | `loging_admin` / `loging_reader` | `EventListResponse` (items+`has_more`+`limit`+`offset`+nullable `total`) | `MISSING_TOKEN`, `INVALID_TOKEN`, `USER_BANNED`, `INSUFFICIENT_ROLE`, `AUTH_SERVICE_NOT_CONFIGURED`, `AUTH_SERVICE_TIMEOUT`, `AUTH_SERVICE_UNREACHABLE`, `AUTH_SERVICE_ERROR`, `INTROSPECT_KEY_NOT_CONFIGURED`, `INTROSPECT_NOT_INITIALIZED`, `RATE_LIMIT_EXCEEDED` |
| GET | `/events/stats` | `loging_admin` / `loging_reader` | `EventStatsResponse` (`total`+`by_severity`+`by_service`+`by_status`+границы окна) | те же 401/403/503 + `RATE_LIMIT_EXCEEDED` |
| GET | `/events/export` | `loging_admin` / `loging_reader` | `text/csv` (заголовок + строки событий; `X-Export-Truncated` при усечении) | те же 401/403/503 + `RATE_LIMIT_EXCEEDED` |
| GET | `/services` | `loging_admin` / `loging_reader` | `ServiceListResponse` (items+`has_more=False`+`limit=null`+`offset=null`) | те же 401/403/503 + `RATE_LIMIT_EXCEEDED` |
| GET | `/services/{service}/events` | `loging_admin` / `loging_reader` | `ServiceEventsResponse` (items+`has_more`+`limit`+`offset`) | те же 401/403/503 + `RATE_LIMIT_EXCEEDED` |

**Окно у `GET /events` vs `GET /events/stats` / `GET /events/export`.** `GET /events` принимает диапазон только через query-параметры `from_time`/`to_time` (полуоткрытый `[from_time, to_time)`); параметра `window_hours` у него нет — переданный `window_hours` молча игнорируется. `GET /events/stats` и `GET /events/export` принимают `from_time`/`to_time` **и** `window_hours` (часы, дефолт 24, диапазон `1..24*366`): если заданы оба края `from_time`/`to_time`, `window_hours` игнорируется; если задан один край — второй достраивается сдвигом на `window_hours`; если не задан ни один — окно `[now - window_hours, now]`. Naive datetime трактуется как UTC.

**Фильтры `stats` / `export`** — те же, что у `GET /events` (`department_id`, `service`, `severity`, `action`, `actor_id`, `target_id`, `status`, `request_id`), сужают выборку под агрегаты/экспорт.

**CSV-экспорт.** `GET /events/export` отдаёт CSV (`Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment`). Колонки совпадают с полями события, `details` сериализуется компактным JSON'ом в последней колонке. Не более `MAX_EXPORT_ROWS` (= 50000) строк на экспорт; при превышении ответ содержит первые строки и заголовок `X-Export-Truncated: true` — сузьте окно или фильтр.

### Rules

| Method | URL | Auth | Response | Возможные `error_code` |
|---|---|---|---|---|
| GET | `/rules` | `loging_admin` | `RuleListResponse` (items+`has_more`+`limit`+`offset`+`total`) | 401/403 auth, `INSUFFICIENT_ROLE`, `RATE_LIMIT_EXCEEDED` |
| GET | `/rules/{rule_id}` | `loging_admin` | `RuleResponse` | + `RULE_NOT_FOUND`, `RATE_LIMIT_EXCEEDED` |
| POST | `/rules` | `loging_admin` | 201 `RuleResponse` | + `RULE_NAME_CONFLICT`, `EFFECT_SEVERITY_REQUIRED`, `EFFECT_SEVERITY_NOT_ALLOWED`, `UNKNOWN_MATCH_ACTION`, `VALIDATION_ERROR`, `INTERNAL_ERROR` |
| PATCH | `/rules/{rule_id}` | `loging_admin` | `RuleResponse` | + `RULE_NOT_FOUND`, `RULE_NAME_CONFLICT`, `EFFECT_SEVERITY_REQUIRED`, `EFFECT_SEVERITY_NOT_ALLOWED`, `UNKNOWN_MATCH_ACTION`, `INTERNAL_ERROR` |
| DELETE | `/rules/{rule_id}` | `loging_admin` | 204 | + `RULE_NOT_FOUND` |

### Retention

| Method | URL | Auth | Response | Возможные `error_code` |
|---|---|---|---|---|
| GET | `/retention` | `loging_admin` | `RetentionPolicyResponse \| null` | 401/403 auth, `RATE_LIMIT_EXCEEDED` |
| PUT | `/retention` | `loging_admin` | 200 `RetentionPolicyResponse` | + `VALIDATION_ERROR` |
| DELETE | `/retention` | `loging_admin` | 204 | 401/403 auth |

### Auth proxy / health

| Method | URL | Auth | Response | Возможные `error_code` |
|---|---|---|---|---|
| POST | `/token` (OAuth2 password proxy в auth_service) | public | 200 `{access_token,...}` | `INVALID_CREDENTIALS` |
| GET | `/health` | public | 200 `{status: ok}` | — |
| GET | `/ready` | public | 200 `{status: ok}` / 503 если retention-watchdog отстал | — |

`/health`, `/ready`, `/token` помечены `include_in_schema=False` — в OpenAPI не светятся.

---

## Идемпотентность

- `POST /events` принимает `idempotency_key` в теле (`EventCreate.idempotency_key`) **и** в заголовке `Idempotency-Key`. Если оба заданы, они должны совпасть после NFKC-нормализации — иначе 400 `VALIDATION_ERROR`. Header — стандарт индустрии (Stripe/GitHub/RFC draft); body — legacy, оставлен для обратной совместимости. Дедуп идёт по паре `(service, idempotency_key)`.
- `POST /services/{service}/events` идемпотентен по природе (upsert по `(service, action)`), отдельного `idempotency_key` нет.
- `DELETE /retention` идемпотентен — повторный вызов на пустом активе всё равно отдаёт 204.

### Frozen-contract: hash считается ПОСЛЕ apply_rules + redact

Дедуп `(service, idempotency_key)` сравнивает не голый payload, а `idempotency_payload_hash` — SHA-256 от уже **отредактированного и прогнанного через rule engine** envelope'а. Это значит:

- **Outbox-retry caller'а safe** только при стабильных входах в hash. Входы: набор `OVERRIDE_SEVERITY`/`SUPPRESS`/`ALLOW`-правил, дефолтная severity-таблица (`_DEFAULT_SEVERITY` в `rule_service.py`) и конфигурация redaction (`utils/redaction.py`).
- **Hot-fix этих структур (правило поменяли, severity-таблицу подправили, redaction-mask расширили) меняет каноничную форму** того же исходного payload'а. Canonical retry того же `(service, idempotency_key)` тогда вернёт **409 `IDEMPOTENCY_KEY_CONFLICT`** — это не баг, а ожидаемое следствие frozen-контракта: в БД уже лежит row с прежним hash'ем.
- **На стороне caller'а** — `IDEMPOTENCY_KEY_CONFLICT` после ingest-config-change всегда трактуй как «событие уже принято в прежней нормализации, ретраить не нужно». Outbox-worker должен зафиксировать send как успешный и идти дальше; ровно так делает `server_worker/src/services/audit_outbox.py::_publish_envelope` (409 → drop из outbox без retry).
- **Свойство «hash от raw payload'а»** в обозримом будущем не планируется: нужно гарантировать, что подавленное SUPPRESS-правилом событие не примется тихо после первого 204, а повторный ingest того же raw payload'а с теми же эффектами правил возвращал тот же row, а не дублировал запись. Frozen-contract сохраняет оба инварианта ценой одной нюансной 409-ветки.

## Pagination shape

| Endpoint | Shape |
|---|---|
| `GET /events` | `items` + `has_more` + `limit` + `offset` + nullable `total` (только при `?include_total=true`) |
| `GET /services/{service}/events` | `items` + `has_more` + `limit` + `offset` + `total` |
| `GET /rules` | `items` + `has_more` + `limit` + `offset` + `total` |
| `GET /services` | `items` + `has_more=False` (всегда) + `limit=null` + `offset=null` + `total=len(items)` (всегда влезает на одну страницу — список заведомо короткий) |

Канонический shape — `EventListResponse` (с `has_more`). Список сервисов отдаётся целиком без пагинации, поэтому `has_more` всегда `False`, а `limit`/`offset` — `null`.

## Связанные документы

- `README.md` — описание сервиса, env-переменные, развёртывание.
- `AUDIT_EVENTS.md` — события self-audit, default severity, шаблоны.
- `obsidian/services/loging_service/` — внутренняя документация (если ветка несёт vault).
