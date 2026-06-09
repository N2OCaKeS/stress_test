# server_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/server/v1`.
> **Статус реализации:** production-ready (test-count'ы — `../TEST_COVERAGE.md`).
> **Аудит-события:** перечислены в `AUDIT_EVENTS.md`.
> **Worker-side dispatch'ы:** код в `src/api/v1/endpoints/worker_dispatch.py` + `installed_packages.py` + `inventory.py`; ставят задачи через `worker_client.dispatch_task` в `dev_server_worker.tasks` + taskiq publish.

## Общие правила

- Все endpoints только по HTTPS в prod (TLS-guard middleware).
- Bearer-токены (user JWT / PAT / bot-token) валидируются через introspect к auth_service на каждом запросе (revalidate из БД, без локального кэша identity).
- Все значимые события идут в `loging_service` (см. `AUDIT_EVENTS.md`).
- Internal-эндпоинты (`/internal/*`) скрыты из OpenAPI (`include_in_schema=False`) — предназначены для server_worker, прав через ту же `entity_permissions` матрицу (роль `worker_bot`).
- Platform-админы (`account_admin`, `loging_admin`) отбиваются `403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED` ещё в middleware (`src/middleware/platform_admin_guard.py`), до handler'а; до user-facing CRUD они не доходят.

### Auth-режимы

- **Bearer (JWT / PAT / bot-token)** — `Authorization: Bearer <token>`. Все user-facing endpoints.
- **Public** — `/health`, `/ready`, `GET /os-versions*` (rate-limit'нутый anonymous read).

### Формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "PERMISSION_DENIED",
  "message": "...",
  "details": {},
  "request_id": "req_123",
  "timestamp": "2026-04-18T12:00:00Z"
}
```

Envelope строится в `src/main.py::app_exception_handler` для всех `AppException` подклассов (`AuthorizationError` 403, `NotFoundError` 404, `ConflictError` 409, `DomainValidationError` 422, `BadRequestError` 400, `GoneError` 410, `ServiceUnavailableError` 503).

`RequestValidationError` (pydantic) → `422 VALIDATION_ERROR`. Любой непойманный `IntegrityError` от БД классифицируется по sqlstate:

| sqlstate | Семантика | HTTP | error_code |
|---|---|---|---|
| `23505` | unique_violation | 409 | `INTEGRITY_VIOLATION_UNIQUE` |
| `23514` | check_violation | 422 | `DOMAIN_CONSTRAINT_VIOLATION` |
| `23503` | foreign_key_violation | 422 | `DOMAIN_CONSTRAINT_VIOLATION` |
| `23502` | not_null_violation | 422 | `DOMAIN_CONSTRAINT_VIOLATION` |
| прочие | unclassified | 500 | `INTERNAL_ERROR` |

Сервисный слой UNIQUE-нарушения ловит сам (`SERVER_DUPLICATE`, `IPMI_DUPLICATE`, ...); fallthrough — это страховка.

### Заголовки ответа

- `X-Request-ID` — корелляционный ID (генерируется middleware если не передан).
- `Retry-After` — на `429`.
- `WWW-Authenticate` — на `401`.
- Security: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, минимальная `Content-Security-Policy`, `Permissions-Policy`, опционально HSTS (`SECURITY_HSTS_ENABLED`).

### Пагинация

List-эндпоинты `/servers`, `/server-accounts`, `/ipmi-controllers`, `/os-versions` поддерживают два envelope'а:

* **Cursor (рекомендуемый):** `?cursor=true&after=<opaque>&limit=<N>` → `{items, next_cursor, has_more}`. `after` опускается на первой странице.
* **Offset (legacy):** `?limit=<N>&offset=<M>` → `{items, total, limit, offset}`. Default `limit=100`, диапазон `1..500`.

`GET /permissions` — non-paginated (матрица ожидаемо ≤200 строк).

### URL-конвенция

Каноничные пути — kebab-case: `/server-accounts`, `/ipmi-controllers`, `/os-versions`, `/installed-packages`. Машинные ключи (`task_kind`, `audit_action`) — snake_case с `<entity>.<verb>` namespace'ом (`installed_packages.list`, `server.power_on`).

Исторический `/ipmi_controllers` (snake) оставлен как hidden алиас на тот же handler — клиенты должны мигрировать на `/ipmi-controllers`.

### Idempotency-Key

Опциональный header (`Idempotency-Key`) поддерживают dispatch-эндпоинты: power on/off/reboot/status, inventory.sync, users.inventory, installed-packages, account provision/update_on_host/deprovision/rotate, server.prepare, ipmi rotate. Long-tail контракт:

- Worker-БД хранит ключ в `VARCHAR(128)` UNIQUE. Запрос с ключом длиннее `IDEMPOTENCY_KEY_MAX_LEN=87` (есть запас под суффикс `:srv_<uuid>` для per-server fan-out'а) → `400 IDEMPOTENCY_KEY_TOO_LONG`.
- Совпадение ключа + target_resource_id с уже стоящей задачей → возвращает тот же `task_id` (`idempotent_hit=True`).
- Совпадение ключа на чужой target_resource_id → `409 IDEMPOTENCY_KEY_REUSE_CONFLICT`.
- Race на UNIQUE между двумя POST'ами (один прошёл SELECT, другой попал в INSERT) → `409 TASK_IDEMPOTENT_CONFLICT`.

POST-CREATE эндпоинты (`POST /servers`, `POST /server-accounts`, `POST /servers/{id}/ipmi`, `POST /os-versions`) `Idempotency-Key` **не читают** — owner-decision: повтор создаёт через UNIQUE-конфликт `409 *_DUPLICATE`, header не поддерживается.

### Rate-limit

Поверх глобального `GLOBAL_RATE_LIMIT` (default 500/min per-IP, исключая `/health`/`/ready`) повешены per-endpoint лимиты (см. README §«Конфиг»):

| Endpoint | ENV | Default |
|---|---|---|
| `POST /servers/{id}/ipmi/credentials/rotate` | `IPMI_CREDENTIALS_ROTATE_RATE_LIMIT` | 5/min |
| `POST /ipmi-controllers/{id}/rotate` | `IPMI_ROTATE_PER_SERVER_RATE_LIMIT` | 5/min |
| `POST /server-accounts/{id}/rotate_password` | `ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT` | 10/min |
| `POST /server-accounts/{id}/rotate` | `MASS_ROTATE_DISPATCH_RATE_LIMIT` | 5/min |
| `POST /servers/{id}/prepare` | `SERVER_PREPARE_RATE_LIMIT` | 3/min |
| `GET /os-versions*` (anonymous) | `OS_VERSIONS_ANON_RATE_LIMIT` | 100/min |
| `/internal/secrets/reencrypt_outbox/{seed,pending}` | `WORKER_POOL_RATE_LIMIT` | 60/min |

Превышение → `429 RATE_LIMIT_EXCEEDED` с `Retry-After`.

### Стабильные `error_code`

Перечень кодов, на которые опираются клиенты SDK (envelope `error_code`). Полные локации см. в endpoint-описаниях ниже и в `src/core/exceptions.py`.

| Код | HTTP | Где появляется |
|---|---|---|
| `PERMISSION_DENIED` | 403 | любой endpoint без нужного action в матрице |
| `DEPARTMENT_ISOLATION` | 403 | попытка работать с чужим department в permissions / create_server |
| `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` | 403 | middleware блок для `account_admin`/`loging_admin` |
| `SERVICE_ACCESS_DENIED` | 403 | у department нет доступа к server_service |
| `SERVER_NOT_FOUND` | 404 | server visibility / cross-dept |
| `ACCOUNT_NOT_FOUND` | 404 | server_account visibility |
| `NO_IPMI_CONTROLLER` | 404 | BMC не зарегистрирован у сервера |
| `TASK_NOT_FOUND` | 404 | cancel неизвестной/cross-dept task'и |
| `SERVER_DUPLICATE` / `IPMI_DUPLICATE` | 409 | UNIQUE на create |
| `SERVER_DECOMMISSIONED` | 409 | dispatch-операция на списанный сервер |
| `TASK_IDEMPOTENT_CONFLICT` | 409 | гонка двух POST с одним Idempotency-Key |
| `IDEMPOTENCY_KEY_REUSE_CONFLICT` | 409 | один Idempotency-Key на разные target_resource_id |
| `TASK_NOT_CANCELLABLE` | 409 | task уже в терминальном статусе |
| `ACCOUNT_HAS_NO_PASSWORD` | 409 | discovered-аккаунт без сохранённого пароля и без `force_password` |
| `NO_LINKED_SERVERS` | 409 | mass-rotate на аккаунт без привязок |
| `IDEMPOTENCY_KEY_TOO_LONG` | 400 | header длиннее `IDEMPOTENCY_KEY_MAX_LEN` |
| `INVALID_CURSOR` | 400 | битый `after` в cursor-пагинации |
| `INVALID_PATTERN` | 400 | shell-glob `pattern` запрещён whitelist'ом |
| `VALIDATION_ERROR` | 422 | pydantic-ошибки валидации тела |
| `DOMAIN_CONSTRAINT_VIOLATION` | 422 | CHECK/FK/NOT NULL fallthrough |
| `INVALID_OS_VERSION` | 422 | FK violation на os_version_id |
| `INVALID_ACTION_FOR_ENTITY` | 422 | grant на неподходящую (entity, action) пару |
| `UNKNOWN_ENTITY_TYPE` | 422 | неизвестный entity_type в `/permissions/{type}` |
| `WEAK_PASSWORD` | 422 | пароль не прошёл политику |
| `RATE_LIMIT_EXCEEDED` | 429 | per-IP или global rate-limit пробит |
| `MASS_ROTATION_TOO_LARGE` | 413 | batch превысил `MASS_ROTATION_MAX_SERVERS` |
| `WORKER_UNREACHABLE` | 503 | dispatch_task: cross-DB engine не отвечает |
| `WORKER_REDIS_NOT_CONFIGURED` | 503 | нет `SERVER_WORKER_REDIS_URL` |
| `WORKER_REDIS_UNAVAILABLE` | 503 | provision-/prepare-creds stash в Redis недоступен |
| `WORKER_DB_NOT_CONFIGURED` | 503 | нет `SERVER_WORKER_DATABASE_URL` |
| `LOGING_SERVICE_UNAVAILABLE` / `LOGING_SERVICE_NOT_CONFIGURED` / `LOGING_SERVICE_AUTH_FAILED` | 503 | drift-чтение через loging |
| `IPMI_ROTATE_USER_FACING_DEPRECATED` | 410 | прямой `/credentials/rotate` от не-bot |
| `DECRYPT_FAILED` / `ENCRYPTION_KEY_MISSING` | 500 | crypto-error при работе с ciphertext'ом |
| `SECRETS_OUTBOX_ROW_NOT_FOUND` | 404 | finalize неизвестного outbox-row |
| `SECRETS_REENCRYPT_FINALIZE_FAILED` | 500 | runtime crash в finalize_done |

---

## Health (`/`)

### `GET /health`

Auth: public. Liveness probe — не пингует БД и upstream'ы.

Response: `{ "status": "ok", "timestamp": "..." }`.

### `GET /ready`

Auth: public. Readiness probe — `SELECT 1` обязателен, best-effort пинги Redis (через `worker_client._prepare_redis_client`) и audit/read pool'ов к loging.

Response: `{ "status": "ready", "timestamp": "...", "db": "ok", "worker_redis": "ok|skipped|<exc>", "audit": "ok|skipped|audit_only" }`. БД-фейл → 500, остальные не валят 200.

---

## Servers (`/servers`)

### `GET /servers`

Auth: Bearer + `(server, *, view)`. Cursor / offset пагинация. Возвращает сервера своего department'а, отсортированы по `created_at DESC, id DESC`.

Errors: `INVALID_CURSOR` (400), `PERMISSION_DENIED` (403), `SERVICE_ACCESS_DENIED` (403), `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (403).

### `POST /servers`

Auth: Bearer + `(server, *, create)`. Body: `ServerCreate` (hostname, ip_address, serial_number, department_id, status, optional storage / ipmi).

`Idempotency-Key` НЕ читается — owner-decision (повтор → `409 SERVER_DUPLICATE`).

Errors: `DEPARTMENT_ISOLATION` (403), `PERMISSION_DENIED` (403), `SERVER_DUPLICATE` (409), `IPMI_DUPLICATE` (409), `INVALID_OS_VERSION` (422).

### `GET /servers/{server_id}`

Auth: Bearer + `(server, *, view)`. Cross-dept → 404 (visibility).

Errors: `SERVER_NOT_FOUND` (404), `PERMISSION_DENIED` (403).

### `GET /servers/{server_id}/drift`

Auth: Bearer + `(server, *, view_drift)`. Окно `[since, now]` (default `now - 24h`). Читает `server_account.drift_detected` события из loging_service.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `LOGING_SERVICE_UNAVAILABLE` / `LOGING_SERVICE_NOT_CONFIGURED` / `LOGING_SERVICE_AUTH_FAILED` (503).

### `PATCH /servers/{server_id}`

Auth: Bearer + `(server, *, update)`. PATCH-диф через `model_dump(exclude_unset=True)`.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DUPLICATE` (409).

### `DELETE /servers/{server_id}`

Auth: Bearer + `(server, *, delete)`. Hard-delete с CASCADE на `server_accounts`, `ipmi_controllers`, `server_disks`. Аудит CRITICAL.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

### `POST /servers/{server_id}/busy`

Auth: Bearer + `(server, *, busy_acquire)`. Атомарный CAS-UPDATE по `busy_state`. Body: `ServerAcquireRequest | null` (`purpose`, `lease_until` опционально).

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_ALREADY_BUSY` (409), `SERVER_DECOMMISSIONED` (409).

### `DELETE /servers/{server_id}/busy`

Auth: Bearer + `(server, *, busy_release)`.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_NOT_BUSY` (409).

### `POST /servers/{server_id}/os-sync`

Auth: Bearer + `(server, *, os_sync)`. Прямое выставление `os_version_id` без worker-inventory.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `INVALID_OS_VERSION` (422).

### `GET /servers/{server_id}/power` (ipmi)

Auth: Bearer + `(server, *, view)`. Кэшированный `power_state` без BMC probe.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

---

## Server-accounts (`/server-accounts`)

### `GET /server-accounts`

Auth: Bearer + `(server_account, *, view)`. Query: `server_id` (обязательный). Cursor / offset пагинация.

Errors: `INVALID_CURSOR` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

### `POST /server-accounts`

Auth: Bearer + `(server_account, *, create)` (+ `grant_sudo` при `has_sudo=true`). Body: логин, `server_ids` (≥1), опциональный пароль (иначе `secrets.token_urlsafe(32)`), sudo/групп/shell/home_dir.

`Idempotency-Key` НЕ читается — owner-decision.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `409` UNIQUE по (server_id, login).

### `GET /server-accounts/{account_id}`

Auth: Bearer + (`view` или `view_password`). Без `view_password` — `password_b64=null`. Раскрытие пароля → CRITICAL audit `server_account.password_revealed` (throttled per actor+account на `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS`).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `DECRYPT_FAILED` (500).

### `PATCH /server-accounts/{account_id}`

Auth: Bearer + `(server_account, *, update)` (+ `grant_sudo` при подъёме `has_sudo: False → True`). При изменении OS-управляемых полей (`has_sudo`/`unix_groups`/`shell`) — fan-out `account.update_on_host` на все серверы с `present_on_server=True`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `POST /server-accounts/{account_id}/servers`

Auth: Bearer + `(server_account, *, update)`. Body: `{server_ids: [...]}`. Привязка к доп. серверам своего dept.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` / `SERVER_NOT_FOUND` (404), `409` login занят на одном из серверов.

### `DELETE /server-accounts/{account_id}/servers`

Auth: Bearer + `(server_account, *, update)`. Отвязка серверов. Нельзя снять последний.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `ACCOUNT_NO_SERVERS` (409).

### `DELETE /server-accounts/{account_id}`

Auth: Bearer + `(server_account, *, delete)`. Hard-delete; на боксе OS-аккаунт не удаляется (для этого — `account.deprovision` dispatch).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `POST /server-accounts/{account_id}/rotate_password`

Auth: Bearer + `(server_account, *, rotate_password)`. Body: опц. `password` (иначе `secrets.token_urlsafe(32)`). Меняет только ciphertext в БД, без SSH-apply. Plaintext НЕ возвращается. CRITICAL audit.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `WEAK_PASSWORD` (422), `RATE_LIMIT_EXCEEDED` (429).

### `POST /server-accounts/{account_id}/rotate` (worker dispatch)

Auth: Bearer + `(server_account, *, rotate_password)`. Query: `server_id?` (точечно vs mass-rotate).

Worker берёт `creds.password`, делает SSH `chpasswd`, callback'ит в `internal/.../rotate`. Mass-rotation: один dispatch на каждый привязанный сервер.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` / `NO_LINKED_SERVERS` (409), `MASS_ROTATION_TOO_LARGE` (413), `RATE_LIMIT_EXCEEDED` (429), `WORKER_UNREACHABLE` / `WORKER_REDIS_NOT_CONFIGURED` (503).

### `POST /server-accounts/{account_id}/provision` (worker dispatch)

Auth: Bearer + `(server_account, *, create)`. Query: `server_id` (обязательный, должен быть привязан), `force_password=false`. Worker: SSH `useradd`.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` / `ACCOUNT_HAS_NO_PASSWORD` (409), `WORKER_REDIS_UNAVAILABLE` / `WORKER_UNREACHABLE` / `WORKER_REDIS_NOT_CONFIGURED` (503).

### `POST /server-accounts/{account_id}/update_on_host` (worker dispatch)

Auth: Bearer + `(server_account, *, update)`. Query: `server_id`. Worker: SSH `usermod` атрибутов (sudo/groups/shell).

Errors: симметрично provision'у (без `ACCOUNT_HAS_NO_PASSWORD`).

### `POST /server-accounts/{account_id}/deprovision` (worker dispatch)

Auth: Bearer + `(server_account, *, delete)`. Query: `server_id`, `remove_home=false`. Worker: SSH `userdel`.

Errors: симметрично update_on_host'у.

---

## IPMI controllers

### `GET /ipmi-controllers`

Auth: Bearer + `(ipmi_controller, *, view)`. Cursor / offset пагинация. Возвращает контроллеры серверов своего dept (JOIN с `servers`).

Алиас: `GET /ipmi_controllers` (snake_case, hidden, для совместимости).

Errors: `INVALID_CURSOR` (400), `PERMISSION_DENIED` (403).

### `POST /servers/{server_id}/ipmi`

Auth: Bearer + `(ipmi_controller, *, create)`. Body: `IpmiControllerCreate` (kind, endpoint_url, username, password). Пароль шифруется через `secrets_service.encrypt()`. UNIQUE(server_id) — 1:1.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `IPMI_DUPLICATE` (409).

### `GET /servers/{server_id}/ipmi`

Auth: Bearer + (`view` или `view_credentials`). С `view_credentials` поле `password_b64` несёт base64(plaintext). CRITICAL audit `ipmi_controller.credentials_revealed`.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` / `NO_IPMI_CONTROLLER` (404), `DECRYPT_FAILED` (500).

### `PATCH /servers/{server_id}/ipmi`

Auth: Bearer + `(ipmi_controller, *, update)`. Меняет kind/endpoint_url/username, без пароля (для пароля — `/credentials/rotate` или `/rotate` dispatch).

Errors: `PERMISSION_DENIED` (403), `NO_IPMI_CONTROLLER` (404).

### `DELETE /servers/{server_id}/ipmi`

Auth: Bearer + `(ipmi_controller, *, delete)`. CRITICAL audit. После удаления power-операции отбиваются `404 NO_IPMI_CONTROLLER`.

Errors: `PERMISSION_DENIED` (403), `NO_IPMI_CONTROLLER` (404).

### `POST /servers/{server_id}/ipmi/credentials/rotate` (legacy)

Auth: Bearer + `(ipmi_controller, *, rotate_credentials)`. **User-facing вызов отбивается 410 GONE**: endpoint писал ciphertext без apply/verify на BMC, что могло убить out-of-band доступ. Каноничный путь — `POST /ipmi-controllers/{id}/rotate` (worker dispatch). Bot-токен оставлен как fallback для legacy worker'ов до миграции на internal callback.

Errors: `PERMISSION_DENIED` (403), `NO_IPMI_CONTROLLER` (404), `IPMI_ROTATE_USER_FACING_DEPRECATED` (410), `RATE_LIMIT_EXCEEDED` (429).

### `GET /servers/{server_id}/ipmi/credentials`

Auth: Bearer + `(ipmi_controller, *, view_credentials)`. Метаданные без plaintext (kind/endpoint_url/username/password_rotated_at). Plaintext только worker'у через `/internal/...`. INFO audit `ipmi_controller.view_credentials_meta`.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` / `NO_IPMI_CONTROLLER` (404).

### `POST /servers/{server_id}/ipmi/power/{on,off,reboot}`

Auth: Bearer + `(server, *, power_{on,off,reboot})`. Dispatch'ит `power.{on,off,reboot}` task через worker. Status 202.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` / `NO_IPMI_CONTROLLER` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_UNREACHABLE` / `WORKER_REDIS_NOT_CONFIGURED` (503).

### `POST /servers/{server_id}/power/status` (worker dispatch)

Auth: Bearer + `(server, *, power_status)`. Live BMC probe через Redfish/ipmitool.

Errors: симметрично power.on (+ требует IPMI-row).

### `POST /ipmi-controllers/{controller_id}/rotate` (worker dispatch)

Auth: Bearer + `(ipmi_controller, *, rotate_credentials)`. **Worker сейчас raise'ит `NotImplementedError`** — storage round-trip не реализован; задача mark_failed + audit failure.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `NO_IPMI_CONTROLLER` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `RATE_LIMIT_EXCEEDED` (429), `WORKER_UNREACHABLE` (503).

---

## Installed packages

### `POST /servers/{server_id}/installed-packages`

Auth: Bearer + `(server, *, view)`. Live-список через SSH (`dpkg-query` / `rpm -qa`) с shell-glob `pattern` (default `*`). Endpoint в БД ничего не пишет.

URL vs action_kind: путь kebab-case, `task_kind` / `audit_action` — `installed_packages.list` (snake, namespace для SIEM/registry).

Errors: `INVALID_PATTERN` / `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_UNREACHABLE` (503).

---

## Inventory

### `POST /servers/{server_id}/inventory/sync` (worker dispatch)

Auth: Bearer + `(server, *, inventory_trigger)`. SSH-сбор hardware-facts (lscpu/lsblk/os-release). Worker postsна `/internal/.../inventory`.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_UNREACHABLE` (503).

### `POST /servers/{server_id}/users/inventory` (worker dispatch)

Auth: Bearer + `(server, *, inventory_trigger)`. **То же** право, что у hardware-инвентаризации — отдельного `users_inventory_trigger` НЕТ. Worker читает `getent passwd` / sudoers, постит на `/internal/.../users/inventory`, server_service reconcile'ит с `server_accounts`.

Errors: симметрично inventory.sync.

### `POST /servers/{server_id}/prepare` (worker dispatch)

Auth: Bearer + `(server, *, update)`. Body: bootstrap-логин + base64 пароля. server_service стэшит креды в Redis под TTL, в payload едет ссылка; worker заходит на сервер, заводит управляющего DBOS-пользователя, кладёт ключ. Callback → `is_managed=True`. CRITICAL audit.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `422` битый base64, `RATE_LIMIT_EXCEEDED` (429), `WORKER_REDIS_UNAVAILABLE` / `WORKER_UNREACHABLE` (503).

---

## OS versions (`/os-versions`)

Глобальный каталог без dept-привязки. Чтение публичное (rate-limit'нутое `OS_VERSIONS_ANON_RATE_LIMIT`), запись — под матрицей.

### `GET /os-versions`

Auth: public. Cursor / offset пагинация. INFO audit `os_version.list_anonymous` для anonymous.

### `GET /os-versions/by-name/{name}`

Auth: public. Карточка по UNIQUE-имени.

Errors: `404`.

### `POST /os-versions`

Auth: Bearer + `(os_version, *, create)`. Body: `OsVersionCreate`.

Errors: `PERMISSION_DENIED` (403), `409` UNIQUE(name).

### `GET /os-versions/{os_version_id}`

Auth: public. Карточка по id.

Errors: `404`.

### `PATCH /os-versions/{os_version_id}`

Auth: Bearer + `(os_version, *, update)`.

Errors: `PERMISSION_DENIED` (403), `404`, `409` UNIQUE(name).

### `DELETE /os-versions/{os_version_id}`

Auth: Bearer + `(os_version, *, delete)`. FK ondelete=RESTRICT — на использование сервером `409 OS_VERSION_IN_USE`.

Errors: `PERMISSION_DENIED` (403), `404`, `OS_VERSION_IN_USE` (409).

---

## Permissions (`/permissions`)

Матрица `entity_permissions` (role × entity_type × action × department).

### `GET /permissions`

Auth: Bearer + `(permission, *, view)`. Non-paginated (ожидаемый размер матрицы < 200). Query: `role?`, `describe=false`.

Envelope: `{items, total, described}`.

Errors: `PERMISSION_DENIED` (403).

### `GET /permissions/catalog`

Auth: Bearer + `(permission, *, view)`. Read-only справочник сущностей/действий с описаниями, `sensitive`/`worker_only` флагами.

### `GET /permissions/{entity_type}`

Auth: Bearer + `(permission, *, view)`. Grants на один entity_type.

Errors: `PERMISSION_DENIED` (403), `UNKNOWN_ENTITY_TYPE` (422).

### `PUT /permissions/{entity_type}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_grant)`. Body: опц. `{target_department_id}` (caller обязан передать свой dept или опустить). Идемпотентно. CRITICAL audit.

Errors: `PERMISSION_DENIED` / `DEPARTMENT_ISOLATION` (403), `PERMISSION_ALREADY_EXISTS` (409), `INVALID_ACTION_FOR_ENTITY` (422).

### `DELETE /permissions/{entity_type}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_revoke)`. Query: `target_department_id?`. CRITICAL audit.

Errors: `PERMISSION_DENIED` / `DEPARTMENT_ISOLATION` (403), `PERMISSION_NOT_FOUND` (404).

---

## Tasks (`/tasks`)

### `POST /tasks/{task_id}/cancel`

Auth: Bearer + `(task, *, cancel)`. Body: `TaskCancelRequest | null` (`reason`). Cross-DB UPDATE в `dev_server_worker.tasks`. Системные task'и (`_SYSTEM_TASK_KINDS` — scheduler-registered heartbeat/sweep/recover/cleanup/reencrypt) требуют `account_admin`.

Errors: `PERMISSION_DENIED` (403), `SYSTEM_TASK_ADMIN_REQUIRED` (403), `TASK_NOT_FOUND` (404), `TASK_NOT_CANCELLABLE` (409).

---

## Internal (worker callbacks, `/internal/*`, hidden из OpenAPI)

`subject_type == 'bot'` на FastAPI-уровне НЕ enforce'ится — полагаемся на матрицу прав (owner-decision 2026-05-30). По seed `view_password`/`rotate_password`/`view_credentials`/`rotate_credentials` выданы только роли `worker_bot`.

Header `X-Target-Department-Id` — опциональный в soft-режиме (default), обязательный при `INTERNAL_REQUIRE_DEPT_HEADER=true`. Несовпадение с реальным `server.department_id` → `403 TARGET_DEPARTMENT_MISMATCH`.

Общий набор кодов: `403 PERMISSION_DENIED` / `TARGET_DEPARTMENT_MISMATCH` / `TARGET_DEPARTMENT_HEADER_REQUIRED`, `404 SERVER_NOT_FOUND` / `ACCOUNT_NOT_FOUND` / `NO_IPMI_CONTROLLER`. Callback-routes дополнительно `422` (битый payload) и `500` `DECRYPT_FAILED` / `ENCRYPTION_KEY_MISSING`.

### `GET /internal/servers/{server_id}/ipmi/credentials`

Возвращает расшифрованный IPMI-пароль worker'у. WARNING audit.

### `GET /internal/servers/{server_id}/accounts/{account_id}/password`

Возвращает расшифрованный пароль server_account. WARNING audit. `409 ACCOUNT_HAS_NO_PASSWORD` для discovered без сохранённого ciphertext'а.

### `POST /internal/servers/{server_id}/accounts/{account_id}/password/rotate`

Worker присылает новый plaintext (после SSH apply). server_service шифрует и сохраняет. CRITICAL audit.

### `POST /internal/servers/{server_id}/inventory`

Hardware-facts от worker'а (после `inventory.sync`).

### `POST /internal/servers/{server_id}/users/inventory`

OS-user inventory от worker'а (после `users.inventory`). server_service reconcile'ит с `server_accounts`: создаёт discovered, обновляет метаданные, эмитит `server_account.drift_detected`.

### `POST /internal/servers/{server_id}/accounts/{account_id}/provision_status`

Worker сообщает результат useradd/usermod/userdel: `present_on_server` true/false.

### `POST /internal/servers/{server_id}/prepared`

Worker сообщает, что bootstrap завершён: `is_managed=True`, `prepared_at=now`. CRITICAL audit.

### `POST /internal/ipmi-controllers/{controller_id}/credentials_rotated`

Storage-first round-trip: worker присылает свежий plaintext + `verified_at` ДО PATCH'а BMC. server_service шифрует, проверяет свежесть verified_at, сохраняет. WARNING audit. `409 CREDENTIALS_ALREADY_APPLIED` на не-pending row.

---

## Internal — secrets re-encryption (`/internal/secrets/*`, hidden)

Контракт для фоновой ре-шифрации секретов worker'ом при bump'е `SERVER_ENCRYPTION_KEY_VERSION`. Доступ — `(server_account, view_password+rotate_password)` И `(ipmi_controller, view_credentials+rotate_credentials)` одновременно (роль `worker_bot`).

### `GET /internal/secrets/migration_status`

Сколько осталось перешифровать. Полностью read-only.

Errors: `SECRETS_MIGRATION_DENIED` (403).

### `POST /internal/secrets/reencrypt_batch`

Legacy sync-путь. Перешифровывает до `limit` записей активной версией.

### `POST /internal/secrets/reencrypt_outbox/seed`

Сканит owner-таблицы и публикует pending outbox-row'ы. Rate-limit `WORKER_POOL_RATE_LIMIT`.

### `GET /internal/secrets/reencrypt_outbox/pending`

`FOR UPDATE SKIP LOCKED` claim batch'а. Rate-limit `WORKER_POOL_RATE_LIMIT`.

### `POST /internal/secrets/reencrypt_outbox/{outbox_id}/done`

Crypto-round-trip + UPDATE owner-row. INFO audit на success, WARNING на skipped (owner_vanished / owner_ciphertext_changed / status_not_processing).

Errors: `SECRETS_MIGRATION_DENIED` (403), `SECRETS_OUTBOX_ROW_NOT_FOUND` (404), `DECRYPT_FAILED` / `ENCRYPTION_KEY_MISSING` / `SECRETS_REENCRYPT_FINALIZE_FAILED` (500).

### `POST /internal/secrets/reencrypt_outbox/{outbox_id}/failed`

Пометить row failed с описанием ошибки. WARNING audit.

### `POST /internal/secrets/reencrypt_outbox/cleanup`

Удалить done-row'ы старше `older_than_hours` (default 24).

---

## Уже задокументированный owner-trade-off

- **POST CREATE без Idempotency-Key.** `POST /servers`, `POST /server-accounts`, `POST /servers/{id}/ipmi`, `POST /os-versions` не читают header. Повтор полагается на UNIQUE — `409 *_DUPLICATE`. Owner-decision; внесение header'а здесь — отдельная задача.
- **`subject_type` для `/internal/*` не enforce'ится** — гейтит только матрица прав; user с расширенным grant'ом теоретически проходит. Owner-decision 2026-05-30.
- **Список envelope union (cursor vs offset) в OpenAPI не виден** — list-эндпоинты декларированы как `response_model=None`, чтобы FastAPI не строил дискриминированную схему. Trade-off: OpenAPI не описывает тип возврата напрямую; SDK-клиенты выбирают envelope по флагу `cursor`.
