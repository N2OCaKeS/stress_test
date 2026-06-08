# secret_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/secret/v1`.
> **Статус реализации:** Phase 1-7 закрыты, Phase 8 (integration) / Phase 9 (k8s) / Phase 10 (docs+infra) в работе. См. `STATUS.md`.
> **Аудит-события:** перечислены в `AUDIT_EVENTS.md`.

## Общие правила

- Все эндпоинты в production только по HTTPS (TLS-guard middleware отбивает cleartext).
- Все user-facing эндпоинты требуют Bearer JWT (`Authorization: Bearer <access_token>`), выдаваемый `auth_service` (`POST /api/auth/v1/login`).
- Identity ресолвится через `/api/auth/v1/authorization/introspect` (pooled httpx, без in-memory кэша). Каждый запрос пересчитывает identity → отзыв роли / бан / удаление dep'а действуют немедленно.
- Internal lifecycle-эндпоинты под `/internal/...` закрыты shared bearer `SERVICE_API_KEY` (или per-caller `SERVICE_API_KEYS`); user-JWT не принимается, introspect отсюда не дёргается. `include_in_schema=False`.
- Health/ready публичны (без auth, без rate-limit).
- Все значимые действия публикуются в `loging_service` — каталог см. в `AUDIT_EVENTS.md`.

### Auth-режимы

- **Bearer (user JWT / PAT / bot-token)** — все user-facing эндпоинты `/credentials/...`. Для bot/PAT identity отдаёт `actor_type=bot` или `actor_type=user`; cred'ы personal scope видны только user-identity.
- **Internal bearer** — `/internal/lifecycle/*` принимают `Authorization: Bearer <SERVICE_API_KEY>` от auth_service / account_admin handler'а.
- **Public** — `/health`, `/ready`.

### Identity gating

Перед scope-проверкой к каждому user-facing запросу применяется последовательность:

1. **Department service-access** — `actor.department_id` должен иметь access к `secret_service` в auth_service. Без этого — `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`.
2. **Cred status** — `status='active'`. Если `blocked` — `410 CREDENTIAL_BLOCKED` с `blocked_reason` / `blocked_at` в `details`.
3. **Scope-зависимая проверка** (`personal` / `department` / `cross_department`) — см. README §«Модель доступа».

### Формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "CREDENTIAL_ACCESS_DENIED",
  "message": "actor has no role_acl on this credential",
  "details": { "cred_id": "cred_..." },
  "request_id": "req_123",
  "timestamp": "2026-04-18T12:00:00Z"
}
```

### Заголовки ответа

- `X-Request-ID` — корелляционный ID (генерируется middleware если не передан).
- `Retry-After` — на `429`.
- `WWW-Authenticate` — на `401` для bearer auth.
- `Strict-Transport-Security` — в production за HTTPS-фронтом (`SECURITY_HSTS_ENABLED`).

### Пагинация

`GET /credentials` отдаёт opaque cursor-пагинацию:

| Параметр | Тип | Default | Диапазон / формат |
|---|---|---|---|
| `limit` | int | `50` | `1..200` |
| `cursor` | str | `null` | Opaque `<iso-timestamp>\|<cred_id>`. Невалидный → `400 INVALID_CURSOR`. |
| `scope` | str | `null` | Фильтр по scope (`personal`/`department`/`cross_department`). |
| `service` | str | `null` | Фильтр по `service`. |
| `status` | str | `null` | Фильтр по `status` (`active`/`blocked`). |

Тело ответа — `{ items: [...], next_cursor: str | null }`. Курсор стабилен между запросами (сортировка по `created_at desc, id desc`).

### Rate-limit

`SLOWAPI_RATE_LIMIT` (default `500/minute`) — глобальный per-IP лимит на всё, кроме `/health` и `/ready`. На `/reveal` отдельный per-actor throttle (5-мин окно).

## Health

### GET /health

Liveness probe. Не трогает БД и не зовёт внешние сервисы.

**Auth:** public.
**Response 200:** `{ "status": "ok", "timestamp": "..." }`.

### GET /ready

Readiness probe + counters. БД обязательна для `status=ok`; на её фейле —
`status=degraded` (HTTP всё равно 200, payload едет оператору). Redis и
audit-counter best-effort. См. README §Healthcheck для семантики полей.

**Auth:** public.
**Response 200:**
```json
{
  "status": "ok",                       // "ok" | "degraded"
  "timestamp": "...",
  "db": true,                           // bool
  "redis_connected": true,              // PING на reveal_throttle redis-клиент
  "secrets_total": 42,                  // SELECT COUNT(*) FROM credentials
  "blocked_total": 3,                   // WHERE status='blocked'
  "audit_dropped_429_total": 0          // per-process
}
```

## Credentials

### GET /credentials

Список видимых credentials с курсорной пагинацией.

**Auth:** Bearer user/bot/PAT.
**Query:** `scope`, `service`, `status`, `limit`, `cursor` (см. §Пагинация).
**Response 200:** `CredentialList = { items: CredentialRead[], next_cursor: str | null }`. `CredentialRead` содержит метаданные кред'ы (`id`, `name`, `service`, `scope`, `owner_user_id`, `owner_dept_id`, `login`, `status`, `created_by`, `created_at`, `updated_at`, `blocked_at`, `blocked_reason`) — без plaintext-secret.
**Error codes:** `400 INVALID_CURSOR`, `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`.

### POST /credentials

Создать credential. Секрет шифруется при сохранении; plaintext в ответе не возвращается.

**Auth:** Bearer user (`actor_type=user`).
**Body (`CredentialCreate`):**

| Поле | Тип | Обязательное | Примечание |
|---|---|---|---|
| `name` | str | да | 1..64 chars, UNIQUE в paре `(owner, service, name)` среди active. |
| `service` | str | да | 1..64 chars, имя внешнего сервиса (`jira`, `confluence`, ...). |
| `scope` | enum | да | `personal` / `department` / `cross_department`. |
| `login` | str \| null | нет | До 4096 chars; null допустим для токен-only кред. |
| `secret` | str | да | 1..8192 chars plaintext, шифруется. |
| `owner_dept_id` | str \| null | для `department`/`cross_department` | Игнорируется для `personal` (owner = текущий user). |

**Response 201:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED` (нет права на create в данном scope/dep'е), `409 NAME_DUPLICATE`, `422 VALIDATION_ERROR` (несовместимость scope/owner_dept_id), `422 PLAINTEXT_TOO_LARGE`, `422 ENCRYPT_INPUT_INVALID`.

### GET /credentials/{cred_id}

Метаданные кред'ы. Без plaintext.

**Auth:** Bearer.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `410 CREDENTIAL_BLOCKED`.

### PATCH /credentials/{cred_id}

Изменить `name` / `login` / `secret`. На `secret` — повторно шифрует.

**Auth:** Bearer (owner / dep_admin / admin secret_service своего dept'а — per scope).
**Body (`CredentialUpdate`):** все поля optional, partial update. Размеры — те же, что в create.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 NAME_DUPLICATE`, `410 CREDENTIAL_BLOCKED`, `422 VALIDATION_ERROR`.

### DELETE /credentials/{cred_id}

Удалить credential. Admin override (не-owner через admin secret_service своего dept'а или account_admin) требует `reason`.

**Auth:** Bearer (owner / dep_admin / admin secret_service своего dept'а / account_admin).
**Body (`AdminDeleteRequest`, optional):** `{ "reason": "..." }` (1..256 chars). Обязателен для admin override, иначе `422 ADMIN_OVERRIDE_REASON_REQUIRED`.
**Response 200:** `OkResponse = { ok: true }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `422 ADMIN_OVERRIDE_REASON_REQUIRED`.

### POST /credentials/{cred_id}/reveal

Расшифровать secret и вернуть plaintext.

**Auth:** Bearer + `RoleACL.can_read` (или owner для personal).
**Body:** пусто.
**Response 200:** `{ "login": str | null, "secret_b64": "<base64(plaintext)>" }`.
**Throttle:** 5-минутное окно per `(actor_id, cred_id)`. Первый reveal — audit `tokens.revealed` **CRITICAL**, повторные в окне — `tokens.revealed_throttled` **INFO** с `count` в details. Окно скользящее.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `410 CREDENTIAL_BLOCKED`, `422 DECRYPT_FAILED` (ciphertext/AAD испорчены), `500 DECRYPT_INTERNAL_ERROR`, `503 ENCRYPTION_KEY_MISSING`.

### POST /credentials/{cred_id}/transfer

Передать ownership заблокированной кред'ы. Только для blocked кред с grants.

**Auth:** Bearer admin secret_service'а владеющего dep'а (personal cred → новый user из числа grantees) или account_admin (cross_dep cred с удалённым owner_dept → новый dep).
**Body (`TransferRequest`):** ровно одно поле:

```json
{ "new_owner_user_id": "usr_..." }
```
или
```json
{ "new_owner_dept_id": "dep_..." }
```

**Response 200:** `CredentialRead` (со снятым `status=blocked`).
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED` (admin не своего dept'а, нет account_admin), `404 CREDENTIAL_NOT_FOUND`, `422 CREDENTIAL_NOT_BLOCKED`, `422 INVALID_TRANSFER_TARGET` (несоответствие scope или target не подходит).

### POST /credentials/{cred_id}/recover

Снять `status=blocked` в окне `BLOCKED_RETENTION_DAYS` (default 30 дней от `blocked_at`).

**Auth:** Bearer admin secret_service'а владеющего dep'а / account_admin.
**Body:** пусто.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `422 CREDENTIAL_NOT_BLOCKED`, `422 RECOVER_WINDOW_EXPIRED`.

## RoleACL

ACL даёт читать (`can_read`) или менять (`can_write`) creds внутри одного департамента.

### POST /credentials/{cred_id}/acl

Выдать `RoleACL`.

**Auth:**
- `personal` cred: только owner (выдаёт в своём департаменте).
- `department` cred: dep_admin владеющего dep'а.
- `cross_department` cred: dep_admin recipient'а, **сначала** должен существовать `DeptGrant(cred_id, recipient_dept_id)`.

**Body (`RoleACLCreate`):**

| Поле | Тип | Обязательное |
|---|---|---|
| `dept_id` | str | да (1..64) |
| `role_name` | str | да (1..64), из `auth.service_role_definitions` |
| `can_read` | bool | default `false` |
| `can_write` | bool | default `false` |

**Response 201:** `RoleACLRead` (с `id`, `granted_by_user_id`, `granted_at`).
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 ROLE_ACL_DUPLICATE`, `422 DEPT_GRANT_REQUIRED`.

### GET /credentials/{cred_id}/acl

Список `RoleACL`.

**Auth:** Bearer (owner / dep_admin владеющего dep'а / recipient dep_admin для cross_dep / admin secret_service владеющего dep'а).
**Response 200:** `RoleACLList = { items: RoleACLRead[] }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`.

### DELETE /credentials/{cred_id}/acl/{acl_id}

Revoke `RoleACL`.

**Auth:** Bearer (owner / dep_admin / admin secret_service владеющего dep'а).
**Response 200:** `OkResponse = { ok: true }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `404 ROLE_ACL_NOT_FOUND`.

## DeptGrant (только `cross_department`)

`DeptGrant` — это разрешение «recipient_dep_admin может выдавать `RoleACL` внутри своего dep'а на эту креду».

### POST /credentials/{cred_id}/dept-grants

Выдать `DeptGrant`.

**Auth:** Bearer (owner dep_admin / admin secret_service владеющего dep'а).
**Body (`DeptGrantCreate`):** `{ "recipient_dept_id": "dep_..." }` (1..64 chars).
**Response 201:** `DeptGrantRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 DEPT_GRANT_DUPLICATE`, `422 DEPT_GRANT_NOT_APPLICABLE` (cred не `cross_department`), `422 DEPT_GRANT_RECIPIENT_IS_OWNER`.

### GET /credentials/{cred_id}/dept-grants

Список `DeptGrant`.

**Auth:** Bearer (owner dep_admin / recipient dep_admin / admin secret_service владеющего dep'а).
**Response 200:** `DeptGrantList = { items: DeptGrantRead[] }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`.

### DELETE /credentials/{cred_id}/dept-grants/{grant_id}

Revoke `DeptGrant`. Каскадно сносит все `RoleACL(cred_id, dept_id=recipient_dept_id)`.

**Auth:** Bearer (owner dep_admin / admin secret_service владеющего dep'а).
**Response 200:** `OkResponse = { ok: true }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `404 DEPT_GRANT_NOT_FOUND`.

## Internal lifecycle (для auth_service / account_admin handler'а)

Закрыты shared bearer `SERVICE_API_KEY` (или per-caller `SERVICE_API_KEYS`). `include_in_schema=False` — не отображаются в публичном OpenAPI. user-JWT здесь не принимается. Транзакция handler'а — atomic per-эндпоинт; ошибки → `500 LIFECYCLE_HANDLER_FAILED` с накопленным списком `details.errors`.

### POST /internal/lifecycle/user-deleted

Cascade на personal-кред удалённого user'а: blocked (если есть `RoleACL.count > 0`) либо hard delete (orphan).

**Auth:** Internal bearer.
**Body (`UserDeletedEvent`):** `{ "user_id": "usr_...", "actor_id": "usr_...", "actor_username": "..." }`.
**Response 200 (`LifecycleSummary`):** `{ "blocked_count": N, "deleted_count": M, "errors": [...] }`.
**Audit:** `tokens.owner_user_deleted_block` (WARNING) на blocked + `tokens.delete` (WARNING) на orphan.
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `500 LIFECYCLE_HANDLER_FAILED`.

### POST /internal/lifecycle/dept-deleted

Cascade на dep'е: и как owner (blocked все его cred'ы), и как recipient (cascade DeptGrant + RoleACL).

**Auth:** Internal bearer.
**Body (`DeptDeletedEvent`):** `{ "dept_id": "dep_...", "actor_id": "usr_...", "actor_username": "..." }`.
**Response 200 (`LifecycleSummary`):** `{ "blocked_count": N, "dept_grants_revoked": K, "role_acls_revoked": M, "errors": [...] }`.
**Audit:** `tokens.owner_dept_deleted_block` (WARNING) + `tokens.dept_recipient_cascade` (CRITICAL).
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `500 LIFECYCLE_HANDLER_FAILED`.

### POST /internal/lifecycle/dept-service-access-revoked

Cascade revoke `DeptGrant`'ов и `RoleACL`'ей где dep — recipient. Свои cred'ы dep'а остаются (через transfer возможен recover).

**Auth:** Internal bearer.
**Body (`DeptServiceAccessRevokedEvent`):** `{ "dept_id": "dep_...", "service": "secret_service", "actor_id": "usr_...", "actor_username": "..." }`. `service != secret_service` → no-op.
**Response 200 (`LifecycleSummary`):** `{ "dept_grants_revoked": K, "role_acls_revoked": M, "errors": [...] }`.
**Audit:** `tokens.dept_revoke_cascade` (CRITICAL).
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `500 LIFECYCLE_HANDLER_FAILED`.

## Каталог error codes

Стабильный каталог — машинные ключи в JSON-envelope под полем `error_code`.

| Code | HTTP | Описание |
|---|---|---|
| `UNAUTHORIZED` | 401 | Bearer отсутствует / невалиден / introspect отверг. |
| `ACCESS_TOKEN_MISSING` | 401 | Заголовок `Authorization` отсутствует. |
| `INVALID_TOKEN_FORMAT` | 401 | Заголовок есть, но формат не `Bearer <token>`. |
| `INTERNAL_AUTH_REQUIRED` | 401 | `/internal/*` без валидного `SERVICE_API_KEY` / `SERVICE_API_KEYS`. |
| `AUTH_SERVICE_UNAVAILABLE` | 503 | introspect-call не дошёл до auth_service (timeout / network / 5xx). |
| `SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` | 403 | У dep'а нет access к `secret_service` (нет записи в `auth.department_service_access`). |
| `CREDENTIAL_ACCESS_DENIED` | 403 | Actor не имеет прав (no ACL / wrong scope / dep mismatch). |
| `CREDENTIAL_NOT_FOUND` | 404 | Не существует ИЛИ cross-dep visibility miss (для нечлена dep'а — `404`, не `403`, чтобы не лікнуть существование). |
| `CREDENTIAL_BLOCKED` | 410 | `status=blocked`. Detail: `{ "blocked_reason": "...", "blocked_at": "..." }`. |
| `CREDENTIAL_NOT_BLOCKED` | 422 | `transfer` / `recover` вызван для active cred'ы. |
| `NAME_DUPLICATE` | 409 | UNIQUE collision `(owner, service, name)` среди active. |
| `ADMIN_OVERRIDE_REASON_REQUIRED` | 422 | Admin override без `reason` в body. |
| `DEPT_GRANT_REQUIRED` | 422 | Создание ACL для cross_dep recipient без существующего `DeptGrant`. |
| `DEPT_GRANT_DUPLICATE` | 409 | UNIQUE collision `(cred_id, recipient_dept_id)`. |
| `DEPT_GRANT_NOT_APPLICABLE` | 422 | `DeptGrant` для не-cross_department кред'ы. |
| `DEPT_GRANT_RECIPIENT_IS_OWNER` | 422 | `recipient_dept_id == owner_dept_id`. |
| `DEPT_GRANT_NOT_FOUND` | 404 | Revoke по неизвестному `grant_id`. |
| `ROLE_ACL_DUPLICATE` | 409 | UNIQUE collision `(cred_id, dept_id, role_name)`. |
| `ROLE_ACL_NOT_FOUND` | 404 | Revoke по неизвестному `acl_id`. |
| `INVALID_TRANSFER_TARGET` | 422 | `new_owner_user_id`/`new_owner_dept_id` не подходит под scope. |
| `RECOVER_WINDOW_EXPIRED` | 422 | `now - blocked_at > BLOCKED_RETENTION_DAYS`. |
| `DECRYPT_FAILED` | 422 | Ciphertext или AAD не сходятся (corruption / tamper). |
| `DECRYPT_INTERNAL_ERROR` | 500 | Внутренняя ошибка при decrypt'е. |
| `ENCRYPTION_KEY_MISSING` | 503 | `SECRET_ENCRYPTION_KEY__v<N>` для версии шифротекста не задан в env (legacy key не подгружен). |
| `ENCRYPT_INPUT_INVALID` | 422 | Plaintext пустой / не str. |
| `PLAINTEXT_TOO_LARGE` | 422 | Plaintext превышает `MAX_PLAINTEXT_BYTES`. |
| `INVALID_CURSOR` | 400 | `cursor` не парсится в `<iso-timestamp>\|<cred_id>`. |
| `ACCOUNT_ADMIN_REQUIRED` | 403 | Эндпоинт требует platform-роли `account_admin`. |
| `SERVICE_ADMIN_REQUIRED` | 403 | Эндпоинт требует service-роли `admin` в secret_service. Per-(dept, service) проверка: guard отсекает не-админов на endpoint-level, дальнейшая привязка к dep'у — в business-логике. |
| `DEPT_ADMIN_REQUIRED` | 403 | Эндпоинт требует platform-роли `department_admin`. |
| `LIFECYCLE_HANDLER_FAILED` | 500 | Внутренняя ошибка lifecycle-handler'а; в `details.errors` — список накопленных ошибок. |
| `VALIDATION_ERROR` | 422 | Pydantic-валидация request body (envelope с `details.errors`). |
| `INTEGRITY_VIOLATION_UNIQUE` | 409 | Не-каталогизированное UNIQUE-нарушение из БД (fallthrough). |
| `DOMAIN_CONSTRAINT_VIOLATION` | 422 | CHECK / FK / NOT NULL нарушение из БД (fallthrough). |
| `RATE_LIMIT_EXCEEDED` | 429 | Per-IP slowapi-лимит (`SLOWAPI_RATE_LIMIT`) или per-actor lockout. |
| `INTERNAL_ERROR` | 500 | Catch-all для нераспознанных DB / runtime ошибок. |
