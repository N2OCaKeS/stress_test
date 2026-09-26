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

`GET /permissions` и `/resource-permissions/by-*` — non-paginated (матрица и инстанс-гранты ожидаемо ≤200 строк).

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

Поверх глобального `GLOBAL_RATE_LIMIT` (default 500/min per-IP, исключая `/health`/`/ready`) повешены per-endpoint лимиты (см. README, раздел «Конфиг»):

| Endpoint | ENV | Default |
|---|---|---|
| `POST /servers/{id}/ipmi/credentials/rotate` | `IPMI_CREDENTIALS_ROTATE_RATE_LIMIT` | 5/min |
| `POST /ipmi-controllers/{id}/rotate` | `IPMI_ROTATE_PER_SERVER_RATE_LIMIT` | 5/min |
| `POST /server-accounts/{id}/rotate_password` | `ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT` | 10/min |
| `POST /server-accounts/{id}/rotate` | `MASS_ROTATE_DISPATCH_RATE_LIMIT` | 5/min |
| `POST /servers/{id}/prepare` | `SERVER_PREPARE_RATE_LIMIT` | 3/min |
| `POST /servers/{id}/clean` | `SERVER_PREPARE_RATE_LIMIT` | 3/min |
| `POST /servers/prepare/bulk` | `BULK_PREPARE_RATE_LIMIT` | 2/min |
| `POST /servers/prepare-batch` | `BULK_PREPARE_RATE_LIMIT` | 2/min |
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
| `OS_VERSION_NOT_FOUND` | 404 | get/update/delete неизвестной os-версии |
| `SERVER_CATEGORY_NOT_FOUND` | 404 | get/update/delete неизвестной категории по мощности |
| `RESOURCE_NOT_FOUND` | 404 | инстанс-ACL: ресурс (server/server_account) не найден / чужой отдел |
| `RESOURCE_PERMISSION_NOT_FOUND` | 404 | инстанс-ACL: revoke грантa, которого нет |
| `SERVER_DUPLICATE` / `IPMI_DUPLICATE` | 409 | UNIQUE на create |
| `OS_VERSION_DUPLICATE` | 409 | UNIQUE(name) на create/update os-версии |
| `RESOURCE_PERMISSION_ALREADY_EXISTS` | 409 | инстанс-ACL: гонка на UNIQUE при grant |
| `SYSTEM_ROLE_IMMUTABLE` | 409 | grant/revoke прав на системную роль `admin`/`guest` (тип-wide или инстанс) |
| `OS_VERSION_IN_USE` | 409 | delete os-версии, на которую ссылается сервер (FK RESTRICT) |
| `SERVER_CATEGORY_DUPLICATE` | 409 | UNIQUE(code) на create/update категории |
| `SERVER_CATEGORY_IN_USE` | 409 | delete категории, на которую ссылается сервер (FK RESTRICT) |
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
| `INVALID_SERVER_CATEGORY` | 422 | `category_id` в create/update сервера не ссылается на существующую категорию |
| `INVALID_ACTION_FOR_ENTITY` | 422 | grant на неподходящую (entity, action) пару |
| `UNKNOWN_ENTITY_TYPE` | 422 | неизвестный entity_type в `/permissions/{type}` |
| `UNKNOWN_RESOURCE_TYPE` | 422 | resource_type вне `{server, server_account}` в `/resource-permissions/*` |
| `ACTION_NOT_INSTANCE_GRANTABLE` | 422 | инстанс-grant на глобально-только action (`create`, callback'и воркера, `view_management_credentials`, `manage_ignored_logins`) |
| `WEAK_PASSWORD` | 422 | пароль не прошёл политику |
| `PREPARE_REQUIRED` | 409 | inventory.sync / users.inventory / installed-packages на неподготовленный сервер (`is_managed=False`) — сначала prepare |
| `MGMT_ROTATION_PENDING` | 409 | ротация управляющих кред при незавершённой предыдущей (`mgmt_creds_pending_apply=True`) — дождаться applied или расстопорить |
| `ACCOUNT_NOT_LINKED` | 404 | prepare account-режим: `account_id` не привязан к этому серверу |
| `ACCOUNT_HAS_NO_PASSWORD` | 409 | prepare account-режим: у выбранного аккаунта нет сохранённого пароля |
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

Auth: Bearer + `(server, *, create)`. Body: `ServerCreate` (hostname, ip_address, serial_number, department_id, optional storage / ipmi / category_id). `status` в теле нет — стартует `unknown`.

`Idempotency-Key` НЕ читается — owner-decision (повтор → `409 SERVER_DUPLICATE`).

Errors: `DEPARTMENT_ISOLATION` (403), `PERMISSION_DENIED` (403), `SERVER_DUPLICATE` (409), `IPMI_DUPLICATE` (409), `INVALID_OS_VERSION` (422), `INVALID_SERVER_CATEGORY` (422).

### `GET /servers/{server_id}`

Auth: Bearer + `(server, *, view)`. Cross-dept → 404 (visibility).

Errors: `SERVER_NOT_FOUND` (404), `PERMISSION_DENIED` (403).

### `GET /servers/{server_id}/drift`

Auth: Bearer + `(server, *, view_drift)`. Окно `[since, now]` (default `now - 24h`). Читает `server_account.drift_detected` события из loging_service.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `LOGING_SERVICE_UNAVAILABLE` / `LOGING_SERVICE_NOT_CONFIGURED` / `LOGING_SERVICE_AUTH_FAILED` (503).

### `PATCH /servers/{server_id}`

Auth: Bearer + `(server, *, update)`. PATCH-диф через `model_dump(exclude_unset=True)`. Тем же путём проставляется категория по мощности — `category_id` (FK на `/server-categories`, `null` снимает категорию); отдельного эндпоинта под это нет.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DUPLICATE` (409), `INVALID_SERVER_CATEGORY` (422).

### `DELETE /servers/{server_id}`

Auth: Bearer + `(server, *, delete)`. Hard-delete с CASCADE на `server_accounts`, `ipmi_controllers`, `server_disks`. Аудит CRITICAL.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

### `POST /servers/{server_id}/busy`

Auth: Bearer + `(server, *, busy_acquire)`. Атомарный CAS-UPDATE по `busy_state`. Body: `ServerAcquireRequest | null` (`purpose`, `lease_until` опционально).

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_ALREADY_BUSY` (409), `SERVER_DECOMMISSIONED` (409).

### `DELETE /servers/{server_id}/busy`

Auth: Bearer + `(server, *, busy_release)`. `testing` не снимается этим путём вообще — только держащий сервис через internal-канал (`release-for-service*`); `acs` снимает только админ (`reservation.ensure_not_acs_locked`).

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_NOT_BUSY` / `SERVER_UPDATING` / `SERVER_TESTING_IN_PROGRESS` / `SERVER_ACS_BUSY` (409).

### `POST /servers/{server_id}/acknowledge-testing-done`

Auth: Bearer + `(server, *, view)` — намеренно тот же гейт, что у `GET /servers/{id}`, не `busy_release`: снять статус «Тестирование завершено» должен уметь любой, кто видит карточку сервера. Снимает `busy_state=testing_done` → `free`, целиком чистит `busy_*` (как обычный release, а не смена стадии). Работает только из `testing_done` — в остальных состояниях 409.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_NOT_TESTING_DONE` (409).

### `POST /servers/{server_id}/os-sync`

Auth: Bearer + `(server, *, os_sync)`. Прямое выставление `os_version_id` без worker-inventory.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `INVALID_OS_VERSION` (422).

### `GET /servers/{server_id}/ipmi/power` (ipmi)

Auth: Bearer + `(server, *, view)`. Кэшированный `power_state` без BMC probe.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

---

## Server-accounts (`/server-accounts`)

### `GET /server-accounts`

Auth: Bearer + `(server_account, *, view)`. Query: `server_id` (обязательный). Cursor / offset пагинация.

Errors: `INVALID_CURSOR` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

### `POST /server-accounts`

Auth: Bearer + `(server_account, *, create)` (+ `grant_sudo` при `has_sudo=true`). Body: логин, `server_ids` (≥1), опциональный `password_b64` (`base64.b64encode(plaintext)`, иначе `secrets.token_urlsafe(32)`; политика проверяется по декодированному plaintext), sudo/групп/shell/home_dir.

`Idempotency-Key` НЕ читается — owner-decision.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `409` UNIQUE по (server_id, login).

### `GET /server-accounts/{account_id}`

Auth: Bearer + (`view` или `view_password`). Без `view_password` — `password_b64=null`. Раскрытие пароля → CRITICAL audit `server_account.password_revealed` (throttled per actor+account на `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS`).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `DECRYPT_FAILED` (500).

### `PATCH /server-accounts/{account_id}`

Auth: Bearer + `(server_account, *, update)` (+ `grant_sudo` при подъёме `has_sudo: False → True`). При изменении OS-управляемых полей (`has_sudo`/`unix_groups`/`shell`) — fan-out `account.update_on_host` на все серверы с `present_on_server=True`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `POST /server-accounts/{account_id}/adopt_from_host`

Auth: Bearer + `(server_account, *, adopt_from_host)` (operator/admin). Body: `{server_id, has_sudo?, unix_groups?, shell?}` — оператор принимает факт-состояние OS-пользователя с конкретного хоста в БД (значения = `found` из diff'а инвентаризации). Применяются ТОЛЬКО присутствующие поля. **DB-only**: fan-out `account.update_on_host` НЕ запускается (в отличие от PATCH — хост уже в этом состоянии). `server_id` обязан быть привязан к аккаунту. Возвращает обновлённую карточку. Audit `server_account.adopted_from_host` (WARNING) с `server_id` / `adopted_fields` / `changes` (old→new). adopt не требует отдельного `grant_sudo` (это фиксация уже-существующего состояния, не подъём привилегии через API).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404 — нет аккаунта / чужой dept / сервер не привязан), `NO_FIELDS_TO_ADOPT` (422 — ни одно поле не передано), `422` невалидные `unix_groups`.

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

Auth: Bearer + `(server_account, *, rotate_password)`. Body: опц. `password_b64` (`base64.b64encode(plaintext)`, иначе `secrets.token_urlsafe(32)`; политика по декодированному plaintext). Меняет ciphertext в БД, поднимает `credentials_pending_apply` и авто-диспатчит `account.update_on_host` (`apply_password: true` + текущий `ssh_public_key`) на серверы с `present_on_server=True` — пароль/ключ пробрасываются на боксы (best-effort). Plaintext НЕ возвращается. Ответ `{id, login, rotated_at, tasks[], skipped[]}` — `tasks`/`skipped` несут результат авто-fan-out'а (куда поехало применение). CRITICAL audit.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `WEAK_PASSWORD` (422), `RATE_LIMIT_EXCEEDED` (429).

### `POST /server-accounts/{account_id}/apply` (worker dispatch)

Auth: Bearer + `(server_account, *, rotate_password)`. Ручной проброс: ставит `account.update_on_host` (`apply_password: true` при наличии пароля + текущий `ssh_public_key`) на все серверы с `present_on_server=True` — доносит сохранённые пароль и ssh-ключ (chpasswd + authorized_keys). Секреты в payload не кладутся: воркер сам резолвит пароль (internal `fetch_account_password` для managed / self-сессия для non-managed). Тот же apply, что авто-запускается после set/rotate. Best-effort: недоступный/списанный/reserved сервер уходит в `skipped`. 202. Ответ `{id, login, tasks[], skipped[]}`. WARNING audit `server_account.apply_credentials`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `POST /server-accounts/import`

Auth: Bearer + `(server_account, *, create)` (+ `grant_sudo` при `has_sudo=true`). Завести в БД незнакомого OS-пользователя, найденного инвентаризацией (элемент `unknown_users` из callback'а). Body: `{server_id, login, has_sudo?, unix_groups?, shell?, source?}`. Пароль НЕ задаётся (на боксе неизвестен; `source=discovered`, `password_encrypted=NULL`); аккаунт привязывается к `server_id` и помечается `present_on_server=True`. WARNING audit `server_account.imported_from_host`.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `ACCOUNT_DUPLICATE` (409 — login уже занят на сервере).

### `GET /server-accounts/ignored-logins`

Auth: Bearer + `(server_account, *, manage_ignored_logins)`. Список игнор-логинов отдела вызывающего (инвентаризация не репортит их как незнакомых). Non-paginated.

Errors: `PERMISSION_DENIED` (403).

### `POST /server-accounts/ignored-logins`

Auth: Bearer + `(server_account, *, manage_ignored_logins)`. Body: `{login, reason?}`. Добавляет логин в ignore-list отдела (перестаёт попадать в `unknown_users` и не дрейфит). UNIQUE(department, login). WARNING audit `server_account.ignored_login_added`.

Errors: `PERMISSION_DENIED` (403), `IGNORED_LOGIN_DUPLICATE` (409).

### `DELETE /server-accounts/ignored-logins/{login}`

Auth: Bearer + `(server_account, *, manage_ignored_logins)`. Убирает логин из ignore-list отдела. INFO audit `server_account.ignored_login_removed`.

Errors: `PERMISSION_DENIED` (403), `IGNORED_LOGIN_NOT_FOUND` (404).

### `POST /server-accounts/{account_id}/recreate_login` (worker dispatch)

Auth: Bearer — **только** platform `department_admin` отдела аккаунта или service-роль `admin` (обычный `update`/operator-грант не проходит). Body: `{login}`. Пересоздаёт живой OS-логин end-to-end: на каждый привязанный сервер `account.deprovision` под старым логином → rename в БД (синхронно с денормализованными копиями на связках) → `account.provision` под новым логином (с паролем/ключом). Серверы decommissioned/недоступные воркеру уезжают в `skipped` (rename в БД всё равно выполняется). 202. CRITICAL audit `server_account.recreate_login`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `ACCOUNT_DUPLICATE` (409 — новый логин занят на одном из серверов).

### `POST /server-accounts/{account_id}/ssh_key` (worker dispatch)

Auth: Bearer + `(server_account, *, update)`. Body: `{ssh_mode, ssh_public_key?, ssh_private_key_b64?}`. `generate` — сервер генерит Ed25519 и возвращает приватный ключ ОДИН раз (`ssh_private_key`); `supply` — клиент передаёт `ssh_public_key` и опц. `ssh_private_key_b64` (если приложен — шифруется и хранится, тогда консоль сможет ходить под аккаунтом). Сохраняет ключ в БД, поднимает `credentials_pending_apply` и диспатчит `account.update_on_host` (текущий `ssh_public_key`, `apply_password` НЕ ставится) на все серверы с `present_on_server=True` (push в `authorized_keys`). 202. WARNING audit `server_account.ssh_key_set`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `422` (`supply` без public-ключа / битый ключ).

### `POST /server-accounts/{account_id}/rotate_ssh_key` (worker dispatch)

Auth: Bearer + `(server_account, *, update)`. Перегенерирует Ed25519-пару (кейс компрометации), сохраняет public + зашифрованный private, **прежний приватный удерживает в `previous_ssh_private_key_encrypted`** (доступен через `/previous_ssh_private_key` на время переходного периода), возвращает новый приватный ключ ОДИН раз, диспатчит `account.update_on_host` на все серверы с `present_on_server=True` (re-push authorized_keys). 202. CRITICAL audit `server_account.ssh_key_rotate`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `GET /server-accounts/{account_id}/ssh_private_key`

Auth: Bearer + `(server_account, *, view_password)` (то же право, что у раскрытия пароля). Расшифровывает и отдаёт приватный SSH-ключ в PEM. Доступен только для сгенерированных сервером ключей (`generate`/`rotate_ssh_key`); у `supply`-ключа без приватной части и у аккаунта без ключа → 404. Per-IP+account reveal-rate-limit (`PASSWORD_REVEAL_RATE_LIMIT`, default 10/min) поверх глобального. CRITICAL audit `server_account.ssh_private_key_revealed`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` / `ACCOUNT_NO_SSH_PRIVATE_KEY` (404), `DECRYPT_FAILED` (422), `RATE_LIMIT_EXCEEDED` (429).

### `GET /server-accounts/{account_id}/previous_ssh_private_key`

Auth: Bearer + `(server_account, *, view_password)`. Зеркало `/ssh_private_key`, но отдаёт удержанный ПРЕЖНИЙ приватный ключ (`previous_ssh_private_key_encrypted`) — доступен на время переходного периода ротации ssh-ключа, пока новый не раскатан на серверы. Нет удержанного ключа → 404 `ACCOUNT_NO_PREVIOUS_SSH_KEY`. Тот же reveal-rate-limit. CRITICAL audit `server_account.reveal_previous_ssh_private_key`.

### `DELETE /server-accounts/{account_id}/previous_ssh_private_key`

Auth: Bearer + `(server_account, *, rotate_password)`. Зануляет удержанный ПРЕЖНИЙ приватный ключ (`previous_ssh_private_key_encrypted` + `previous_ssh_key_rotated_at`) — оператор зовёт вручную, когда добил недоступные в момент ротации серверы и прежний ключ больше не нужен. Идемпотентна: нет удержанного ключа → тот же `200 {ok: true}`, без ошибки. Авто-очистки по callback'у нет — previous держится до этой ручной очистки либо следующей ротации ssh-ключа. INFO audit `server_account.clear_previous_ssh_key` (`details.cleared` — реально ли что-то удалили).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` / `ACCOUNT_NO_PREVIOUS_SSH_KEY` (404), `DECRYPT_FAILED` (422), `RATE_LIMIT_EXCEEDED` (429).

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

Auth: Bearer + `(ipmi_controller, *, create)`. Body: `IpmiControllerCreate` (kind, endpoint_url, username, `password_b64` = `base64.b64encode(plaintext)`). Пароль декодируется, проходит политику по plaintext и шифруется через `secrets_service.encrypt()`. UNIQUE(server_id) — 1:1.

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

Auth: Bearer + `(ipmi_controller, *, rotate_credentials)`. **Любой вызов отбивается 410 GONE** (включая bot/worker_bot — fallback'а нет): endpoint писал ciphertext без apply/verify на BMC, что могло убить out-of-band доступ. Каноничный путь — `POST /ipmi-controllers/{id}/rotate` (worker dispatch с BMC apply + verify); ciphertext сохраняется только через internal callback `POST /internal/ipmi-controllers/{id}/credentials_rotated`.

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

Auth: Bearer + `(ipmi_controller, *, rotate_credentials)`. Worker заходит на BMC старым паролем, генерит новый, применяет (Redfish PATCH / ipmitool), под новым паролем делает read-only verify и только после verify шлёт ciphertext в `/internal/ipmi-controllers/{id}/credentials_rotated` (storage round-trip, проверка свежести `verified_at`). Verify не прошёл — storage не коммитится, задача FAILED.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `NO_IPMI_CONTROLLER` (404), `SERVER_DECOMMISSIONED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `RATE_LIMIT_EXCEEDED` (429), `WORKER_UNREACHABLE` (503).

---

## Installed packages

### `POST /servers/{server_id}/installed-packages`

Auth: Bearer + `(server, *, view)`. Live-список через SSH (`dpkg-query` / `rpm -qa`) с shell-glob `pattern` (default `*`). Endpoint в БД ничего не пишет. Требует prepare (`is_managed=True`) — probe идёт по управляющему ключу; неподготовленный сервер — `409 PREPARE_REQUIRED`.

URL vs action_kind: путь kebab-case, `task_kind` / `audit_action` — `installed_packages.list` (snake, namespace для SIEM/registry).

Errors: `INVALID_PATTERN` / `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `PREPARE_REQUIRED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_UNREACHABLE` (503).

### `GET /servers/{server_id}/packages/history`

Auth: Bearer + `(server, *, view)` (то же право, что у самого запроса пакетов). История прошлых `installed_packages.list`-задач сервера — чтобы видеть уже полученные результаты, не гоняя SSH-probe заново. Источник — `dev_server_worker.tasks` (тот же, что `GET /tasks`), но server-scoped: тащит запрошенный `pattern`/`patterns` из task-payload'а и найденные `packages`/`package_count` из task.result.

Сортировка — `enqueued_at DESC`. Пагинация: `limit` (1..100, default 20) + `offset`; общее число — в заголовке `X-Total-Count`. Видна вся история сервера (не только свои запросы); незавершённые (`queued`/`running`) — с пустым `packages`. Read-only: prepare/decommissioned-гейтов нет. Глубина ограничена retention'ом worker'а (`tasks.cleanup_completed_old`).

Response: `list[{task_id, status, pattern, patterns, requested_by, requested_at, finished_at, package_count, packages, last_error}]`.

Audit: `installed_packages.history` (INFO) — только на denied/not-found.

Errors: `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404).

### `POST /servers/installed-packages/bulk` (worker dispatch)

Auth: Bearer + `(server, *, view)` (проверяется один раз на весь батч). Массовый live-запрос пакетов: на каждый сервер из `server_ids` диспатчит `installed_packages.list` (тот же per-server SSH-probe, что у одиночного). Паттерны общие на весь батч: `patterns` (список 1..20, матч по ЛЮБОМУ — OR) или одиночный `pattern` (back-compat). Per-server гейты (не валят батч): dept-visibility (`not_found`), prepare (`prepare_required`), decommissioned. Reserve-гейт не нужен (read-only). Async-модель: dispatch только ставит задачи, реальные пакеты лежат в `task.result` каждого сервера (в ответе `packages` пуст) — UI добирает поллингом `GET /tasks/{task_id}`. Cap — `INSTALLED_PACKAGES_BULK_MAX_SERVERS` (default 50). INFO audit `installed_packages.list` per сервер.

Errors: `INVALID_PATTERN` (400), `PERMISSION_DENIED` (403), `BULK_PACKAGES_TOO_LARGE` (413).

### `POST /servers/packages/bulk-action`

Auth: Bearer + `(server, *, manage_packages)`. Массовая ИЗМЕНЯЮЩАЯ операция: на каждый сервер из `server_ids` диспатчит `installed_packages.{install|remove|update}` — worker под управляющим пользователем по SSH с sudo выполняет `apt-get`/`dnf`/`apk`.

Body: `{server_ids:[...], action:"install"|"remove"|"update", packages:[...]}`. `packages` обязателен для install/remove; для update опционален (пусто = обновить всё). Имена пакетов — строгий allow-list `[A-Za-z0-9._+-]` (без glob), валидируется схемой → `422`.

Per-server гейты (не валят батч): dept-visibility (`not_found`), prepare (`prepare_required`), reserve (`reserved` — занятый чужим оператором), decommissioned (`decommissioned`). Response: `{action, packages, requested, dispatched, results:[{server_id, hostname, status, task_id?}]}`. Реальный результат UI добирает поллингом `GET /tasks/{task_id}`.

Audit: `server.packages_install` / `_remove` / `_update` (WARNING — мутация на боксе). Cap серверов — `INSTALLED_PACKAGES_BULK_MAX_SERVERS` (дефолт 50).

Errors: `PERMISSION_DENIED` (403), `BULK_PACKAGES_TOO_LARGE` (413), `422` (валидация тела/имён пакетов).

---

## Inventory

### `POST /servers/{server_id}/inventory/sync` (worker dispatch)

Auth: Bearer + `(server, *, inventory_trigger)`. SSH-сбор hardware-facts (lscpu/lsblk/os-release). Worker заходит по ключу под управляющим пользователем (`management_user`) и постит на `/internal/.../inventory`.

Требует prepare: сервер обязан быть `is_managed=True` (worker заходит по управляющему ключу). Неподготовленный сервер — `409 PREPARE_REQUIRED`. Путь self-сессии под паролем привязанного аккаунта (`account_id`-резолв до prepare) снят.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `PREPARE_REQUIRED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_UNREACHABLE` (503).

### `POST /servers/{server_id}/users/inventory` (worker dispatch)

Auth: Bearer + `(server, *, inventory_trigger)`. **То же** право, что у hardware-инвентаризации — отдельного `users_inventory_trigger` НЕТ. Worker читает `getent passwd` / sudoers, постит на `/internal/.../users/inventory`, server_service reconcile'ит с `server_accounts`.

Требует prepare (`is_managed=True`) — симметрично `inventory/sync`. Неподготовленный сервер — `409 PREPARE_REQUIRED`.

Errors: симметрично inventory.sync, включая `PREPARE_REQUIRED` (409).

### `POST /servers/{server_id}/prepare` (worker dispatch)

Auth: Bearer + `(server, *, update)`. Body — два взаимоисключающих режима (ровно один): `{account_id}` (server_service резолвит привязанный server_account и расшифровывает его пароль — нужно ещё право `view_password`; account_id не привязан → `404 ACCOUNT_NOT_LINKED`, нет пароля → `409 ACCOUNT_HAS_NO_PASSWORD`) либо ручной `{username_b64, password_b64, ssh_private_key_b64?}` (base64, ssh-ключ опционален). server_service стэшит креды в Redis под TTL, в payload едет ссылка; worker заходит на сервер, заводит управляющего DBOS-пользователя, кладёт ключ. Callback → `is_managed=True`. CRITICAL audit (account-режим дополнительно эмитит `server_account.bootstrap_resolved`).

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403, в т.ч. отсутствие `view_password` в account-режиме), `SERVER_NOT_FOUND` / `ACCOUNT_NOT_LINKED` (404), `SERVER_DECOMMISSIONED` / `ACCOUNT_HAS_NO_PASSWORD` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `422` (битый base64 / нарушение режима / слабый ручной пароль), `RATE_LIMIT_EXCEEDED` (429), `WORKER_REDIS_UNAVAILABLE` / `WORKER_UNREACHABLE` (503).

### `POST /servers/prepare-batch` (worker dispatch)

Auth: Bearer + `(server, *, update)` (проверяется один раз на весь батч). Массовый prepare с per-server выбором режима: `items: [{server_id, account_id?} | {server_id, username_b64, password_b64, ssh_private_key_b64?}]` — у каждого сервера account-режим (по умолчанию) либо ручной, ровно один. account-режим дополнительно требует `view_password` на учётку. Per-server путь идентичен single-prepare (idempotency-replay → decommissioned-gate → resolve creds → Redis-stash → dispatch). Ответ симметричен mass-rotation: `{batch_id, dispatched: [{server_id, server_name, task_id, status}], failed: [{server_id, server_name, reason}]}`. Один битый сервер уходит в `failed`, не валит батч; глобальная недоступность воркера помечает упавший `worker_unreachable`, остаток `not_attempted`. CRITICAL audit на каждый сервер.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403, весь батч), `BULK_PREPARE_TOO_LARGE` (413, превышен `BULK_PREPARE_MAX_SERVERS`), `422` (битый base64 / слабый пароль / нарушение режима / дубли server_id), `RATE_LIMIT_EXCEEDED` (429). Per-server `reason` (в `failed`): `not_found_or_cross_dept` / `decommissioned` / `idempotent_conflict` / `idempotency_key_reuse_conflict` / `account_has_no_password` / `account_not_found` / `account_not_linked` / `permission_denied` / `worker_unreachable` / `not_attempted`.

### `POST /servers/{server_id}/clean` (worker dispatch)

Auth: Bearer + `(server, *, update)` (как prepare). Оркестрация очистки после переустановки ОС: четыре флага (`unbind_accounts`, `update_os_version`, `rerun_prepare`, `run_inventory_sync`) выполняются в фиксированном порядке unbind → rerun_prepare → update_os_version → run_inventory_sync, каждый переиспользует существующий путь. `rerun_prepare=true` требует блок `prepare` (те же поля/режимы, что single-prepare). `update_os_version` берёт `os_version_id` (`null` сбрасывает). Ответ — per-action сводка `{server_id, unbind_accounts, rerun_prepare, update_os_version, run_inventory_sync}`, каждое `{status: done|dispatched|skipped|failed, task_id?, reason?, detail?}`. CRITICAL audit `server.clean` + по-действенные эмиты (`server_account.unlink_servers` / `server_account.deprovision` / `server.prepare` / `server.update_os_version` / `server.inventory_sync`).

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` (409), `422` (ни одного действия / `rerun_prepare` без `prepare` / битый base64 / слабый пароль), `RATE_LIMIT_EXCEEDED` (429). Per-action `failed.reason`: `permission_denied` / `prepare_required` / `invalid_os_version` / `worker_unreachable` / `account_has_no_password` / `account_not_linked` / `idempotent_conflict` и т.п.

### `POST /servers/prepare/bulk` (worker dispatch, legacy)

Auth: Bearer + `(server, *, update)` (проверяется один раз на весь батч). **Legacy** массовый prepare: поддерживает только ручной режим bootstrap-кред на каждый сервер (`items: [{server_id, username_b64, password_b64, ssh_private_key_b64?}]`) — без account-режима (для per-server выбора account/ручной используйте `POST /servers/prepare-batch`). Те же per-server гейты, что у single-prepare (visibility/dept-isolation, decommissioned-check, Redis-stash, dispatch). Один битый сервер уходит в `skipped` с `reason` и не валит батч; глобальная недоступность воркера на первом сервере отбивает весь запрос 503. Дубли `server_id` → 422. Rate-limit `BULK_PREPARE_RATE_LIMIT` (default 2/min). CRITICAL audit на каждый сервер.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403, весь батч), `BULK_PREPARE_TOO_LARGE` (413), `422` (битый base64 / слабый пароль / дубли server_id), `RATE_LIMIT_EXCEEDED` (429), `503` (worker недоступен на первом сервере).

### `POST /servers/{server_id}/management-credentials/rotate` (worker dispatch)

Auth: Bearer + `(server, *, update)` (тот же гейт, что у prepare). Сервер обязан быть prepared (`is_managed`). Dispatch'ит `server.rotate_management_creds`: server_service генерит новую Ed25519-пару + пароль управляющего пользователя, переносит текущий ciphertext в `previous_mgmt_*` (анти-локаут), пишет новый в `mgmt_*`, ставит `mgmt_creds_pending_apply=True` и кладёт новый материал в Redis-stash. Worker заходит ДЕЙСТВУЮЩИМ ключом (internal-fetch отдаёт previous, пока pending), ставит новый pubkey + chpasswd, проверяет вход новым ключом, затем POST'ит `/internal/servers/{id}/management-credentials/applied`, после чего server_service снимает pending и зануляет previous. 202. CRITICAL audit `server.management_creds_rotated`.

Пока предыдущая ротация не подтверждена (`mgmt_creds_pending_apply=True`) — новая отбивается `409 MGMT_ROTATION_PENDING`: повторный перенос `mgmt_*`→`previous_*` затёр бы реально стоящий на боксе ключ ещё не раскатанным материалом. Расстопорить — дождаться applied-callback'а или сбросить застрявшую ротацию вручную.

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `PREPARE_REQUIRED` / `MGMT_ROTATION_PENDING` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_REDIS_UNAVAILABLE` / `WORKER_UNREACHABLE` / `WORKER_REDIS_NOT_CONFIGURED` (503).

---

## ACS snapshots (полные снимки диска физического сервера)

Своей таблицы снимков нет — ACS (внешняя Clonezilla-обёртка) сам хранит директорию. Платформенный кил-свитч `AcsSettings.enabled` + per-department opt-in `AcsDepartmentAccess` (`/settings/acs`, account_admin) — оба гейта поверх обычной action-матрицы, проверяются `_ensure_acs_available`. `busy_state=acs` сериализует конкурентные операции над сервером.

### `POST /servers/{server_id}/acs-snapshots` (worker dispatch)

Auth: Bearer + `(server, *, acs_snapshot_create)` (инстанс-грантуемое). Ставит `acs.snapshot_create`: ACS снимает полный образ диска (`stand_name=hostname`). Сервер обязан быть prepared (`PREPARE_REQUIRED`, если нет). `busy_state=acs` снимается в любом исходе callback'а — create не переписывает диск.

Errors: `PERMISSION_DENIED` (403, в т.ч. `ACS_DEPARTMENT_NOT_ENABLED`), `SERVER_NOT_FOUND` / `OS_VERSION_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `SERVER_IS_VMS_HUB` / `SERVER_RESERVED` / `SERVER_UPDATING` / `SERVER_ACS_BUSY` / `PREPARE_REQUIRED` / `TASK_IDEMPOTENT_CONFLICT` (409), `ACS_DISABLED` / `WORKER_UNREACHABLE` (503).

### `POST /servers/{server_id}/acs-snapshots/restore` (worker dispatch)

Auth: Bearer + тип-wide `(server, *, acs_snapshot_restore)` — из `_NON_INSTANCE_ACTIONS`, инстанс-грант невозможен, только системный `admin`. Ставит `acs.snapshot_restore`: ACS полностью переписывает диск снимком версии `os_version_id`. Необратимо. Требует заранее заведённый bootstrap-пароль версии (`PUT /os-versions/{id}/bootstrap-password`) — без него после reimage авто-`server.prepare` зайти будет нечем. Prepared-gate НЕ применяется (restore — это и есть recovery-путь для сломанного/не managed сервера). `busy_state=acs` при успехе держится до завершения авто-prepare (callback `record_acs_snapshot_restore_done`).

Errors: `PERMISSION_DENIED` (403, тип-wide, в т.ч. `ACS_DEPARTMENT_NOT_ENABLED`), `SERVER_NOT_FOUND` / `OS_VERSION_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `SERVER_IS_VMS_HUB` / `SERVER_RESERVED` / `SERVER_UPDATING` / `SERVER_ACS_BUSY` / `ACS_BOOTSTRAP_PASSWORD_MISSING` / `TASK_IDEMPOTENT_CONFLICT` (409), `ACS_DISABLED` / `WORKER_UNREACHABLE` (503).

### `GET /servers/{server_id}/acs-snapshots`

Auth: Bearer + `(server, *, acs_snapshot_list)` (инстанс-грантуемое). Живой directory listing: читает `ACSClient.list_snapshots` (все снимки всех серверов на ACS) и фильтрует по префиксу `{hostname}-` этого сервера; `version_name` — хвост имени после префикса. Ничего не диспатчит воркеру, читает ACS синхронно. Ошибки ACS (timeout/unreachable/HTTP-ошибка) пробрасываются как есть.

Response: `{hostname, snapshots: [{name, version_name, normalized_version}]}`, отсортировано по `name`. `normalized_version` — хвост после `normalize_os_version_name` (`1710rc52` → `1.7.10.52`), в этой форме каталог хранит `os_version.name`. Префикс — `{hostname}-` с дефисом: снимки `LowServer2-…` серверу `LowServer` не принадлежат. Реализация фильтра и выбора — `src/services/acs_snapshot_lookup.py`.

Audit: `server.acs_snapshot_list` (INFO) — на success и failure.

Errors: `PERMISSION_DENIED` (403, в т.ч. `ACS_DEPARTMENT_NOT_ENABLED`), `SERVER_NOT_FOUND` (404), `ACS_DISABLED` / `ACS_TIMEOUT` / `ACS_UNREACHABLE` / `ACS_ERROR` (503).

### `POST /servers/acs-snapshots/create-batch` (worker dispatch)

Auth: Bearer. `ACS_SNAPSHOT_CREATE` инстанс-грантуем, поэтому право проверяется НА КАЖДЫЙ `server_id` отдельно (не один раз на весь батч, в отличие от prepare-batch с `update`). Body: `{server_ids:[...], os_version_id}` — одна версия каталога на весь батч (снимок создаётся под одной РЦ для всех выбранных серверов). Per-server гейты идентичны single-dispatch (decommissioned / vms-hub / acs-busy / reservation / prepared-gate / ACS-доступность / каталог-версия) и не валят батч — уходят в `failed`. Глобальная недоступность ACS/воркера помечает упавший сервер причиной, остаток — `not_attempted`. Фронт сам исключает VMS-hub сервера из `server_ids` чекбоксом «выбрать все, кроме VMS-hub» — бэкенд всё равно защищён (`SERVER_IS_VMS_HUB`), если хаб всё же попал в список.

Response: `{batch_id, dispatched: [{server_id, server_name, task_id, status}], failed: [{server_id, server_name, reason}]}`. `reason`: `not_found_or_cross_dept` / `permission_denied` / `decommissioned` / `server_is_vms_hub` / `reserved` / `updating` / `acs_busy` / `prepare_required` / `acs_disabled` / `acs_department_not_enabled` / `os_version_not_found` / `idempotent_conflict` / `worker_unreachable` / `not_attempted`.

Errors: `ACS_SNAPSHOT_BATCH_TOO_LARGE` (413, превышен `bulk_prepare_max_servers` — лимит переиспользован из prepare-batch), `422` (дубли server_id).

### `POST /servers/acs-snapshots/restore-batch` (worker dispatch)

Auth: Bearer + тип-wide `(server, *, acs_snapshot_restore)`, проверяется ОДИН раз на весь батч (симметрично prepare-batch с `update`) — в отличие от create-batch. Body/ответ/cap идентичны create-batch, `task_kind=acs.snapshot_restore`, `reason` дополнительно несёт `bootstrap_password_missing`. Необратимо для каждого сервера в списке.

Errors: `PERMISSION_DENIED` (403, весь батч), `ACS_SNAPSHOT_BATCH_TOO_LARGE` (413), `422` (дубли server_id).

---

## OS versions (`/os-versions`)

Глобальный каталог без dept-привязки. Чтение доступно любому аутентифицированному актору (токен обязателен, без проверки доступа департамента к server_service), запись — под матрицей.

### `GET /os-versions`

Auth: Bearer (любой аутентифицированный актор, включая платформенные роли). Cursor / offset пагинация.

Errors: `ACCESS_TOKEN_MISSING` (401), `INVALID_CURSOR` (400).

### `GET /os-versions/by-name/{name}`

Auth: Bearer (любой аутентифицированный актор). Карточка по UNIQUE-имени.

Errors: `ACCESS_TOKEN_MISSING` (401), `OS_VERSION_NOT_FOUND` (404).

### `POST /os-versions`

Auth: Bearer + `(os_version, *, create)`. Body: `OsVersionCreate`.

Errors: `PERMISSION_DENIED` (403), `OS_VERSION_DUPLICATE` (409).

### `GET /os-versions/{os_version_id}`

Auth: Bearer (любой аутентифицированный актор). Карточка по id.

Errors: `ACCESS_TOKEN_MISSING` (401), `OS_VERSION_NOT_FOUND` (404).

### `PATCH /os-versions/{os_version_id}`

Auth: Bearer + `(os_version, *, update)`.

Errors: `PERMISSION_DENIED` (403), `OS_VERSION_NOT_FOUND` (404), `OS_VERSION_DUPLICATE` (409).

### `DELETE /os-versions/{os_version_id}`

Auth: Bearer + `(os_version, *, delete)`. FK ondelete=RESTRICT — на использование сервером `409 OS_VERSION_IN_USE`.

Errors: `PERMISSION_DENIED` (403), `OS_VERSION_NOT_FOUND` (404), `OS_VERSION_IN_USE` (409).

---

## Server categories (`/server-categories`)

Платформенный каталог категорий стендов по мощности (`low_server`, `middle_server`, `high_server`, `workstation` — сидятся миграцией; новые заводятся через API). Без dept-привязки. Чтение доступно любому аутентифицированному актору, запись — под матрицей `(server_category, *)`. Категория проставляется серверу обычным `PATCH /servers/{id}` полем `category_id`.

### `GET /server-categories`

Auth: Bearer (любой аутентифицированный актор). Offset-пагинация, envelope `{items, total, limit, offset}`, порядок — по `code`.

Errors: `ACCESS_TOKEN_MISSING` (401).

### `GET /server-categories/by-code/{code}`

Auth: Bearer (любой аутентифицированный актор). Карточка по UNIQUE-коду.

Errors: `ACCESS_TOKEN_MISSING` (401), `SERVER_CATEGORY_NOT_FOUND` (404).

### `POST /server-categories`

Auth: Bearer + `(server_category, *, create)`. Body: `ServerCategoryCreate` (`code` — слаг `[a-z][a-z0-9_]*`, `label`, опц. `description`).

Errors: `PERMISSION_DENIED` (403), `VALIDATION_ERROR` (422), `SERVER_CATEGORY_DUPLICATE` (409).

### `GET /server-categories/{category_id}`

Auth: Bearer (любой аутентифицированный актор). Карточка по id.

Errors: `ACCESS_TOKEN_MISSING` (401), `SERVER_CATEGORY_NOT_FOUND` (404).

### `PATCH /server-categories/{category_id}`

Auth: Bearer + `(server_category, *, update)`.

Errors: `PERMISSION_DENIED` (403), `SERVER_CATEGORY_NOT_FOUND` (404), `SERVER_CATEGORY_DUPLICATE` (409), `VALIDATION_ERROR` (422).

### `DELETE /server-categories/{category_id}`

Auth: Bearer + `(server_category, *, delete)`. FK `servers.category_id` ondelete=RESTRICT — на использование сервером `409 SERVER_CATEGORY_IN_USE`.

Errors: `PERMISSION_DENIED` (403), `SERVER_CATEGORY_NOT_FOUND` (404), `SERVER_CATEGORY_IN_USE` (409).

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

Auth: Bearer + `(permission, *, permission_grant)`. Body: опц. `{target_department_id}` (caller обязан передать свой dept или опустить). Идемпотентно. Системные роли `admin`/`guest` неизменяемы → `409 SYSTEM_ROLE_IMMUTABLE`. CRITICAL audit.

Errors: `PERMISSION_DENIED` / `DEPARTMENT_ISOLATION` (403), `SYSTEM_ROLE_IMMUTABLE` / `PERMISSION_ALREADY_EXISTS` (409), `INVALID_ACTION_FOR_ENTITY` (422).

### `DELETE /permissions/{entity_type}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_revoke)`. Query: `target_department_id?`. Системные роли `admin`/`guest` неизменяемы → `409 SYSTEM_ROLE_IMMUTABLE`. CRITICAL audit.

Errors: `PERMISSION_DENIED` / `DEPARTMENT_ISOLATION` (403), `PERMISSION_NOT_FOUND` (404).

---

## Resource permissions (`/resource-permissions`)

Инстанс-уровневый ACL — точечные гранты роли на КОНКРЕТНЫЙ ресурс (`server` / `server_account`) поверх тип-wide матрицы `entity_permissions`. Каждая строка несёт `effect` ∈ `{allow, deny}`. Эффективное право роли на ресурс резолвится с precedence: инстанс `deny` перекрывает тип-wide базу для этой роли, инстанс `allow` добавляет право, иначе действует тип-wide матрица. Итог для caller'а — OR по его ролям (deny одной роли убирает только её вклад, другая роль может разрешить).

- **Системные роли `admin`/`guest` неизменяемы.** Любой grant/revoke на них (как инстанс, так и тип-wide через `/permissions`) → `409 SYSTEM_ROLE_IMMUTABLE`. `admin` = всё, `guest` = `server.view` — фиксированы. `worker_bot` системным не считается (его гранты управляются).

- **resource_type** ∈ `{server, server_account}` (вне этого набора → `422 UNKNOWN_RESOURCE_TYPE`). `os_version` (глобальный каталог), `ipmi_controller` (живёт под сервером), `permission`/`task` инстанс-ACL не поддерживают.
- **Инстанс-гранты всегда per-department:** `department_id` строки = отдел самого ресурса. System-wide инстанс-грантов через API нет.
- **Грантуются только инстанс-привязанные действия.** `create` и callback-действия воркера (`inventory_submit` / `provision_on_host` / `prepare_callback`), а также `view_management_credentials` и `manage_ignored_logins` остаются только в глобальном слое `/permissions` (попытка выдать их инстанс-грантом → `422 ACTION_NOT_INSTANCE_GRANTABLE`).
- **Инстанс-грантуемые действия:**
  * `server`: `view`, `update`, `delete`, `busy_acquire`, `busy_release`, `os_sync`, `power_on`, `power_off`, `power_reboot`, `power_status`, `inventory_trigger`, `console`, `view_drift`, `manage_packages`.
  * `server_account`: `view`, `update`, `delete`, `view_password`, `rotate_password`, `grant_sudo`, `provision`, `deprovision`, `adopt_from_host`, `console`.
- **Доступ к управлению** — как у `/permissions`: платформенный `account_admin` — мета-админ (bypass ролевой проверки и dept-isolation), остальным нужны `(permission, *, view)` / `permission_grant` / `permission_revoke` своего отдела. Ресурс обязан быть в отделе актора (иначе `404 RESOURCE_NOT_FOUND` — существование не раскрываем). Все мутации пишутся в audit как CRITICAL.

### `GET /resource-permissions/by-resource/{resource_type}/{resource_id}`

Auth: Bearer + `(permission, *, view)` либо account_admin. Список инстанс-грантов на конкретный ресурс. Envelope `{items, total}`.

Errors: `PERMISSION_DENIED` (403), `UNKNOWN_RESOURCE_TYPE` (422).

### `GET /resource-permissions/by-role/{role}`

Auth: Bearer + `(permission, *, view)` либо account_admin. Срез всех инстанс-грантов одной роли. Query: `resource_type?` (сужает выдачу до одного типа). Envelope `{items, total}`.

Errors: `PERMISSION_DENIED` (403), `UNKNOWN_RESOURCE_TYPE` (422 — если передан невалидный `resource_type`).

### `PUT /resource-permissions/{resource_type}/{resource_id}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_grant)` либо account_admin. Выдать инстанс-грант `action` роли `role` на ресурс. Query: `effect` ∈ `{allow, deny}` (default `allow`). Идемпотентно по effect: повтор того же `(scope, effect)` → возврат строки без изменений и без audit; смена effect (allow↔deny) на существующей строке апдейтит её (id сохраняется) и эмитит audit. Scope строки = отдел ресурса. Ответ — `ResourcePermissionResponse` (`{id, resource_type, resource_id, role, action, effect, department_id, granted_by, created_at, updated_at}`). CRITICAL audit `resource_permission.grant` (в `details` — `effect`).

Errors: `PERMISSION_DENIED` (403), `RESOURCE_NOT_FOUND` (404 — ресурс не найден / чужой отдел), `SYSTEM_ROLE_IMMUTABLE` (409 — role ∈ `{admin, guest}`) / `RESOURCE_PERMISSION_ALREADY_EXISTS` (409 — гонка на UNIQUE), `UNKNOWN_RESOURCE_TYPE` / `ACTION_NOT_INSTANCE_GRANTABLE` / `INVALID_EFFECT` (422 — `effect` вне `{allow, deny}`; обычно ловится pattern-валидацией FastAPI как `VALIDATION_ERROR`).

### `DELETE /resource-permissions/{resource_type}/{resource_id}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_revoke)` либо account_admin. Снять инстанс-грант. Ответ — `OkResponse`. CRITICAL audit `resource_permission.revoke`.

Errors: `PERMISSION_DENIED` (403), `RESOURCE_NOT_FOUND` (404 — ресурс не найден / чужой отдел) / `RESOURCE_PERMISSION_NOT_FOUND` (404 — такой строки нет), `SYSTEM_ROLE_IMMUTABLE` (409 — role ∈ `{admin, guest}`), `UNKNOWN_RESOURCE_TYPE` (422).

### `POST /resource-permissions/{resource_type}/{source_resource_id}/propagate`

Auth: Bearer + `(permission, *, permission_grant)` (для `mode=mirror` — дополнительно `permission_revoke`) либо account_admin. Копирует ВСЕ инстанс-гранты ресурса-образца на список целей того же типа.

Body: `{"target_resource_ids": [...], "mode": "merge"|"mirror"}` (default `merge`; `target_resource_ids` — 1..500 элементов, сам образец из списка отбрасывается). `mode=merge` добавляет на каждую цель недостающие `(role, action)` образца с их `effect`, существующие не трогает; `mode=mirror` приводит цель к точной копии образца — добавляет недостающее, переключает `effect` существующих под образец И удаляет лишнее. `effect` каждого гранта переносится. Идемпотентно. Все цели обязаны быть в отделе образца, иначе `404 RESOURCE_NOT_FOUND`. CRITICAL audit `resource_permission.propagate`.

Ответ — `ResourcePropagateResponse`: `{source_resource_id, resource_type, mode, source_grant_count, targets: [{resource_id, added, removed}]}` (`removed` всегда 0 при `merge`).

Errors: `PERMISSION_DENIED` (403 — нет `permission_grant`; для `mirror` ещё `permission_revoke`), `RESOURCE_NOT_FOUND` (404 — образец или любая цель не найдены / чужой отдел), `UNKNOWN_RESOURCE_TYPE` (422), `VALIDATION_ERROR` (422 — пустой/слишком большой `target_resource_ids`, невалидный `mode`).

---

## Tasks (`/tasks`)

### `GET /tasks`

Auth: Bearer + `(task, *, view)`. Страница worker-task'ов из `dev_server_worker.tasks`, сортировка `enqueued_at DESC`; общее число под фильтром — в заголовке `X-Total-Count`. Тело — `list[TaskRead]`. Фильтры: `status`, `kind` (task_kind), `server_id`, `created_by` (user_id инициатора). Пагинация: `limit` (1..200, default 50) + `offset`. Видны задачи серверов своего отдела; читатель без service-роли `admin`/`operator` (и не department_admin) видит только свои (`created_by`); инфра-задачи без сервера — только `admin`/`operator`. Platform-админам вход закрыт middleware'ом.

Errors: `PERMISSION_DENIED` (403), `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (403). На denied — audit `task.view`.

### `GET /tasks/{task_id}`

Auth: Bearer + `(task, *, view)`. Деталь одной задачи (`TaskRead` с полным `result` и `last_error`). Dept-visibility: чужой отдел / невидимый сервер маскируются под 404; читатель видит только свои задачи (чужая того же отдела → 404). Инфра-задача без сервера видна только `admin`/`operator`.

Errors: `PERMISSION_DENIED` (403), `TASK_NOT_FOUND` (404). На denied — audit `task.view`.

### `POST /tasks/{task_id}/cancel`

Auth: Bearer + `(task, *, cancel)`. Body: `TaskCancelRequest | null` (`reason`). Cross-DB UPDATE в `dev_server_worker.tasks`. Системные task'и (`_SYSTEM_TASK_KINDS` — scheduler-registered heartbeat/sweep/recover/cleanup/reencrypt) требуют `account_admin`.

Errors: `PERMISSION_DENIED` (403), `SYSTEM_TASK_ADMIN_REQUIRED` (403), `TASK_NOT_FOUND` (404), `TASK_NOT_CANCELLABLE` (409).

---

## Console (`/servers/{server_id}/console`)

### `WS /servers/{server_id}/console/ws`

Auth: Bearer — токен берётся из `Authorization: Bearer` либо из subprotocol `bearer.<token>` (браузерный WS API не даёт задать произвольные заголовки). Middleware-стек на WebSocket не выполняется, поэтому introspect + ban/service-access делаются в самом handler'е. Query: `account_id` (обязателен — под какой учёткой коннектиться). Интерактивная PTY-консоль через Redis pub/sub-мост к worker'у: подключение идёт под кредами выбранного `server_account`, не под управляющим ключом — поэтому сервер НЕ обязан быть prepared.

Доступ: ролевой `(server, *, console)` ЛИБО на выбранном аккаунте роль с `console` или `view_password` (держатель `view_password` подключается без отдельного console-гранта). Чувствительное — оператору по умолчанию не выдано. Department-scope сервера и аккаунта проверяется.

Session-события: `ssh_console.session_open` / `session_close` (INFO). Per-команда — `ssh_console.command` (эмитит worker на PTY-мосте, INFO / WARNING на ненулевом exit).

WS-close коды: `4400` bad request, `4401` `ACCESS_TOKEN_MISSING`/невалидный токен, `4403` forbidden, `4404` not found, `4409` conflict, `4503` worker недоступен, `1000` нормальное закрытие.

---

## Console macros (`/console-macros`)

Личные и системные (department-wide) заготовки команд для интерактивной консоли. См. `services/console_macro.py`.

### `GET /console-macros`

Auth: Bearer (любой аутентифицированный актор отдела). Личные макросы вызывающего + системные его отдела; сортировка — сначала личные, потом системные, внутри группы по `display_order`. Чтение без аудита.

Errors: `ACCESS_TOKEN_MISSING` (401).

### `POST /console-macros`

Auth: Bearer. Body: `ConsoleMacroCreate`. `is_system=false` — личный (привязан к вызывающему, может любой вошедший); `is_system=true` — системный (общий в отделе), только `department_admin` своего отдела. INFO audit `console_macro.create`.

Errors: `PERMISSION_DENIED` (403 — системный без department_admin).

### `PATCH /console-macros/{macro_id}`

Auth: Bearer. Личный — только владелец; системный — `department_admin` отдела. Чужой/скрытый макрос → 404 (существование не светится). INFO audit `console_macro.update`.

Errors: `PERMISSION_DENIED` (403), `404` (макрос не найден или скрыт).

### `DELETE /console-macros/{macro_id}`

Auth: Bearer. Те же права, что у PATCH. INFO audit `console_macro.delete`.

Errors: `PERMISSION_DENIED` (403), `404`.

---

## Management-user config (`/management-user-config`)

Платформенный singleton-конфиг управляющей учётки, под которой система ходит на подготовленные серверы. Под `account_admin` — явное исключение в `platform_admin_guard`.

### `GET /management-user-config`

Auth: Bearer + платформенный `account_admin`. Текущий login + пер-режимные группы/bootstrap-команды (нет строки → дефолт: `login=dbos`, пустые режимы).

Errors: `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED` (401), `ACCOUNT_ADMIN_REQUIRED` (403).

### `PUT /management-user-config`

Auth: Bearer + `account_admin`. Body: `ManagementUserConfigUpdate` (`login` без значения — без изменений; присланные режимы в `modes` заменяются целиком, остальные сохраняются). При изменении `modes`/`login` фан-аутит недеструктивный high-priority `management_user_sync` (re-bootstrap групп/команд/ключа) на все `is_managed`-серверы платформы независимо от отдела. Смена `login` сам rename НЕ выполняет — в ответе поднимается `login_changed`/`rename_pending` (cutover — отдельная фаза). WARNING audit `management_user_config.update`; per-server — `management_user_config.sync`; обрезка хвоста фан-аута — `management_user_sync_fanout.truncated`.

Errors: `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED` (401), `ACCOUNT_ADMIN_REQUIRED` (403), `422` (невалидный login / группы / пустые команды).

---

## Internal (worker callbacks, `/internal/*`, hidden из OpenAPI)

`subject_type == 'bot'` на FastAPI-уровне НЕ enforce'ится — полагаемся на матрицу прав (owner-decision 2026-05-30). По seed `view_password`/`rotate_password`/`view_credentials`/`rotate_credentials` выданы только роли `worker_bot`.

Header `X-Target-Department-Id` — обязательный и enforce'ится безусловно (единственный cross-dept гард глобального worker-бота; отдел самого бота не участвует). Отсутствует → `403 TARGET_DEPARTMENT_HEADER_REQUIRED`; не совпал с реальным `server.department_id` → `404` (маска not-found `SERVER_NOT_FOUND`/`ACCOUNT_NOT_FOUND`/`NO_IPMI_CONTROLLER`, чтобы 403/404 не работали enumeration-oracle'ом), audit `reason=target_department_mismatch`.

Общий набор кодов: `403 PERMISSION_DENIED` / `TARGET_DEPARTMENT_HEADER_REQUIRED`, `404 SERVER_NOT_FOUND` / `ACCOUNT_NOT_FOUND` / `NO_IPMI_CONTROLLER` (сюда же маскируется target_department_mismatch). Callback-routes дополнительно `422` (битый payload) и `500` `DECRYPT_FAILED` / `ENCRYPTION_KEY_MISSING`.

### `GET /internal/servers/{server_id}/ipmi/credentials`

Возвращает расшифрованный IPMI-пароль worker'у. WARNING audit.

### `GET /internal/servers/{server_id}/accounts/{account_id}/password`

Возвращает расшифрованный пароль server_account. WARNING audit. `409 ACCOUNT_HAS_NO_PASSWORD` для discovered без сохранённого ciphertext'а.

### `POST /internal/servers/{server_id}/accounts/{account_id}/password/rotate`

Worker присылает новый plaintext (после SSH apply). server_service шифрует и сохраняет. CRITICAL audit.

### `GET /internal/servers/{server_id}/management/credentials`

Auth: `(server, *, view_management_credentials)` — worker_bot. Расшифровывает per-server управляющие креды (`mgmt_ssh_private_key_encrypted` + `mgmt_password_encrypted`) и отдаёт воркеру `{management_user, ssh_private_key, password}` для managed-операций. Пока `mgmt_creds_pending_apply=True` и есть previous-материал — отдаётся он (рабочий на боксе, `source=previous`), иначе текущий; это и есть авто-вывод старой версии после успешного applied-callback'а. WARNING audit `server.management_credentials_revealed`.

Errors: `SERVER_NOT_FOUND` / `MANAGEMENT_CREDS_NOT_FOUND` (404 — сервер не prepared), `DECRYPT_FAILED` (422), `ENCRYPTION_KEY_MISSING` (500).

### `POST /internal/servers/{server_id}/management-credentials/applied`

Auth: `(server, *, prepare_callback)` — worker_bot. Applied-callback ротации управляющих кред: worker подтвердил, что новый материал применён и проверен на боксе. server_service снимает `mgmt_creds_pending_apply`, зануляет previous-зеркала и проставляет `mgmt_creds_rotated_at` — с этого момента fetch отдаёт текущий материал (старая версия выведена). CRITICAL audit `server.management_creds_rotated` (ветка b).

### `POST /internal/servers/{server_id}/inventory`

Hardware-facts от worker'а (после `inventory.sync`).

### `POST /internal/servers/{server_id}/users/inventory`

OS-user inventory от worker'а (после `users.inventory`). server_service reconcile'ит с `server_accounts`: создаёт discovered, обновляет метаданные, эмитит `server_account.drift_detected`. Ответ несёт `diffs: [{account_id, login, fields: {<имя>: {expected, found}}}]` — структурированный per-account attribute-drift для привязанных аккаунтов (БД не перетирается, данные для ручного ревью через `adopt_from_host`); worker кладёт `diffs` в `task.result`.

### `POST /internal/servers/{server_id}/accounts/{account_id}/provision_status`

Worker сообщает результат useradd/usermod/userdel: `present_on_server` true/false.

### `POST /internal/servers/{server_id}/power-state`

Worker пишет результат живой пробы питания (`power.status`) в кэш сервера. Body: `{power_state: "on"|"off"|"unknown", source: "bmc"|"ping"|"ssh"}`. server_service проставляет `power_state` + `power_state_source` + `power_state_checked_at` (UTC). Auth: `(server, *, prepare_callback)` — worker_bot-only. Idempotent best-effort. INFO audit `server.power_state_updated`. Ответ: `{ok, power_state, checked_at}`.

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

## Internal — ops runners (`/internal/migration_status`, hidden)

Отдельный канал от worker_bot'а: ops-runner'ы ходят с shared-secret'ом из `SERVER_INBOUND_SERVICE_API_KEYS` под header'ом `X-Service-Identity: <identity>` — без user/department/матрицы прав. Доступ ограничен whitelist'ом identity (`require_internal_caller`). Реализация — `src/api/v1/endpoints/ops.py`.

### `GET /internal/migration_status`

Auth: shared-secret + `X-Service-Identity: rotation_runner`. Read-only сводка по `secrets_reencrypt_outbox` + per-column legacy-residue. Payload идентичен `/internal/secrets/migration_status` (но та защищена worker_bot scope'ом, потому не подходит ops-runner'у). Используется `scripts/k8s/rotate_master_key.sh --auto-finalize` для дожимания `outbox.pending == 0` и `remaining_legacy_total == 0` перед drop'ом старой версии ключа. INFO audit `ops.migration_status_read`.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403).

### `POST /internal/encryption/rotate`

Auth: shared-secret + `X-Service-Identity: rotation_runner`. Рантайм-ротация мастер-ключа без простоя: новый ключ (генерит auth_service `POST /admin/service-keys/generate`) присылает rotation-runner. Новая версия становится активной (новые секреты сразу под ней), старые версии остаются читаемыми (материал в keystore), фоновая ре-шифрация публикуется через reencrypt-outbox. Идемпотентно (повтор с тем же материалом не плодит версию). CRITICAL audit `ops.encryption_rotate`.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403), `ROTATE_KEY_INVALID` (422 — `new_key_b64` не base64 / не 32 байта).

### `POST /internal/encryption/retire/{version}`

Auth: shared-secret + `X-Service-Identity: rotation_runner`. Убирает старую версию мастер-ключа из keystore — только когда на версии 0 строк (полная ре-шифрация завершена) и она не активна. После retire материал недоступен, расшифровать данные этой версии станет нельзя. Дублирует автоматический авто-вывод: не-активную осушённую версию система выводит сама (см. `encryption.auto_retire`), ручной вызов — резерв. CRITICAL audit `ops.encryption_retire`.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403), `KEYSTORE_CANNOT_RETIRE_ACTIVE` / `KEYSTORE_VERSION_IN_USE` (409).

---

## Internal — бронь от имени сервиса (`/internal/servers/{id}/*-for-service`, hidden)

Тот же shared-secret канал, что и ops, но по серверам: сервис-потребитель бронирует стенд на всё время своей работы. Whitelist identity — `testing_service`, `acs`. Имя держателя берётся ТОЛЬКО из провалидированной `X-Service-Identity` — тело запроса своё имя заявить не может. Держатель пишется в `servers.busy_service_name` при `busy_actor_type=service`; `busy_user_id` при этом пуст (CHECK `ck_servers_busy_actor`). Bot-пользователь в auth_service под это не заводится. Реализация — `src/api/v1/endpoints/internal_service_reservation.py`.

### `POST /internal/servers/{id}/acquire-for-service`

Auth: shared-secret + `X-Service-Identity: testing_service|acs`. Тело: `busy_state` (`busy`/`testing`/`acs`, default `acs`), `busy_note` (опционально, ≤512), `takeover` (bool, default `false`). Атомарный CAS по `busy_state='free'` — сервер, занятый кем угодно (человеком или другим сервисом), отдаёт 409. При `takeover=true` и `busy_state` ∈ {`busy`, `testing_done`} бронь атомарно (row-lock) переписывается на вызывающий сервис (`busy_actor_type=service`, `busy_user_id` обнуляется), ответ дополнительно содержит `previous_holder` (`busy_state`, `busy_user_id`, `busy_service_name`, `busy_note`); без takeover поле `null`. `updating` / `acs` / `testing` не отнимаются — 409 даже с `takeover=true`. Проверку прав админа делает вызывающий сервис. INFO audit `server.acquired_for_service`; при takeover — WARNING `server.reservation_taken_over`.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403), `SERVER_NOT_FOUND` (404), `SERVER_ALREADY_BUSY` / `SERVER_DECOMMISSIONED` (409).

### `POST /internal/servers/batch-status`

Auth: shared-secret + `X-Service-Identity: testing_service|acs`. Тело: `server_ids` (0..500) и `vm_ids` (0..500) — смешанный список пула, хотя бы один id в сумме. ВМ отвечаются в `vms` (`vm_id`, `found`, те же поля брони в терминах `busy_state` серверов — см. `/internal/vms/{id}/acquire-for-service` — и ping гостя). Один элемент на каждый запрошенный id: `server_id`, `found` (отсутствующий сервер — `found=false`, не 404 на весь батч), `busy_state`, `busy_service_name`, `busy_user_id`, `busy_actor_type` (`user`/`service`), `busy_note`, `ping_reachable`, `ping_checked_at`. Поля держателя брони нужны `testing_service` для 409 `STAND_BUSY` («кто держит стенд»). Чистое чтение, audit не эмитится.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403), 422 на пустой/слишком длинный список.

### `POST /internal/servers/{id}/release-for-service`

Auth: та же. Снимает бронь и возвращает сервер в `free`, только если её держит именно этот caller (`busy_service_name == identity`); чужая бронь — 409 `SERVER_RESERVED_BY_OTHER`. Чистит `busy_user_id`/`busy_service_name`/`busy_actor_type`/`busy_note`/`busy_since`. INFO audit `server.released_for_service`.

Errors: 401/403 как выше, `SERVER_NOT_FOUND` (404), `SERVER_NOT_BUSY` / `SERVER_RESERVED_BY_OTHER` (409).

### `POST /internal/servers/{id}/release-for-service-as-done`

Auth: та же. Снимает бронь этого caller'а, но не в `free`, а в `busy_state=testing_done` — `busy_actor_type`/`busy_service_name`/`busy_note` сохраняются как контекст «кто тестировал», не сбрасываются. Используется `testing_service`'ом, когда очередь стенда опустела: кто-то должен явно принять стенд через человеческий `POST /servers/{id}/acknowledge-testing-done`, прежде чем он снова станет `free`. Чужая бронь — 409 `SERVER_RESERVED_BY_OTHER`, как у обычного release-for-service. INFO audit `server.released_for_service`.

Errors: 401/403 как выше, `SERVER_NOT_FOUND` (404), `SERVER_NOT_BUSY` / `SERVER_RESERVED_BY_OTHER` (409).

### `GET /internal/servers/{id}/acs-snapshots`

Auth: та же. Тот же живой список снимков ACS этого сервера, что у `GET /servers/{id}/acs-snapshots` (тот же ответ `{hostname, snapshots: [{name, version_name, normalized_version}]}`), но без permission/department-гейтов. Потребитель — `testing_service.queue.enqueue()`: перед постановкой проверяет, что снимок РЦ есть (сравнение `os_version.name` с `normalized_version`). Ошибки ACS пробрасываются как есть.

Errors: 401/403 как выше, `SERVER_NOT_FOUND` (404), `ACS_DISABLED` / `ACS_TIMEOUT` / `ACS_UNREACHABLE` / `ACS_ERROR` (503).

### `POST /internal/servers/{id}/service-status`

Auth: та же. Тело: `busy_state` (обязательно), `busy_note` (опционально; `null` оставляет прежнюю заметку). Переключает стадию внутри уже взятой этим же caller'ом брони — целевой сценарий `acs` → `testing` после получения кред от `prepare-for-test`. `busy_since` не двигается: он отмеряет всю бронь. INFO audit `server.service_status_changed`.

Errors: 401/403 как выше, `SERVER_NOT_FOUND` (404), `SERVER_NOT_BUSY` / `SERVER_RESERVED_BY_OTHER` (409).

---

## Internal — `prepare-for-test` (асинхронный контракт, hidden)

Пайплайн restore→prepare→провижн тестового пользователя→смена ядра→смена режима безопасности→ребут (часы, не секунды) — асинхронный контракт с 202+callback, не запрос-ответ. Реализация — `src/api/v1/endpoints/internal_prepare_for_test.py`, оркестрация — `src/services/prepare_for_test.py`. План — `emm/obsidian/ALLTA MIGRATION.md`.

### `POST /internal/servers/{id}/prepare-for-test`

Auth: shared-secret + `X-Service-Identity: testing_service|acs` (тот же канал, что у брони от имени сервиса). Тело: `os_version_id`, `kernel` (обязан быть в `os_versions.kernels`), `mode` (`orel`/`smolensk`), `test_username` (default `u`), `requested_by_department_id` (опционально), `correlation_id` (ключ идемпотентности), `test_account_credential_id` (опционально, тестовая учётка отдела в secret_service — scope=service, service=test_account, владелец — отдел сервера; проверяется до restore, раскрывается на шаге `user_provision`; логин/пароль/публичный ключ ставятся из неё, `test_username` игнорируется, callback учётных данных не несёт; без поля — прежние случайные пароль и ключ). Отвечает сразу 202 `{prepare_request_id, status}` — реальный исход приходит callback'ом в `TESTING_SERVICE_URL`. Повтор с тем же `correlation_id` возвращает уже существующий запрос, второй пайплайн не стартует. Ядро вне каталога РЦ — терминальный `failed` сразу (`failed_step=kernel_change`), без единого похода к стенду. Перед `acs.snapshot_restore` снимок ищется в живом списке ACS по `{hostname}-{os_version.name}` с нормализацией версии: в restore уходит фактический хвост найденного снимка (`1710rc52`, а не `1.7.10.52`); из нескольких кандидатов — точное совпадение с `os_version.name`, иначе первый по имени. Снимка нет или ACS недоступна — терминальный `failed` (`failed_step=restore`, `error="ACS_SNAPSHOT_NOT_FOUND: ACS snapshot '<hostname>-<version>' not found"` либо код ошибки ACS) без вызова `restore-backup`. INFO audit `server.prepare_for_test_requested`.

Errors: `SERVICE_IDENTITY_REQUIRED` / `INVALID_SERVICE_TOKEN` (401), `SERVICE_IDENTITY_NOT_ALLOWED` (403), `SERVER_NOT_FOUND` (404), `SERVER_ALREADY_BUSY` / `SERVER_DECOMMISSIONED` / `SERVER_IS_VMS_HUB` / `PREPARE_FOR_TEST_ALREADY_RUNNING` (409), `WORKER_*` (503).

### `GET /internal/servers/{id}/prepare-for-test/{prepare_request_id}`

Auth: та же. Полное состояние запроса (`status`, `stage`, `failed_step`, `error`, счётчик и результат доставки callback'а) — для добора результата, если исходящий callback не доехал. Секретов (пароль/ключ учётки исполнения теста) не отдаёт — они уходят только в callback.

Errors: 401/403 как выше, `PREPARE_REQUEST_NOT_FOUND` / `SERVER_NOT_FOUND` (404).

### `POST /internal/servers/{id}/prepare-for-test-done`

Auth: обычный internal-канал воркера (bearer worker_bot + матрица `entity_permissions`, право `(server, prepare_callback)` — существующий грант, без нового). Тело: `prepare_request_id`, `succeeded`, `failed_step` (на провале), `error` (на провале — причина; на успехе — необязательное non-fatal предупреждение, например расхождение режима безопасности перед сменой). На успехе отправляет исходящий callback с учёткой исполнения теста в `TESTING_SERVICE_URL`; на провале — с `failed_step`+`error`. WARNING audit `server.prepare_for_test_completed`.

Errors: `PERMISSION_DENIED` / `TARGET_DEPARTMENT_HEADER_REQUIRED` (403), `SERVER_NOT_FOUND` / `PREPARE_REQUEST_NOT_FOUND` (404).

Поле `preparation` (`full` по умолчанию / `revert_only`): `revert_only` — restore/откат, учётка и ядро без `mode_switch` и без шага `stand_setup` (`stand_setup` из тела отбрасывается; воркеру уходит `skip_mode_switch=true`). Легаси — клиент FreeIPA (`allta_app_full/backup_image.py::freeipa_authentication_test`, `run_provision.modes = False`).

Поле `skip_pam_fix` (`false` по умолчанию): `true` — шаг `pam_fix` (`pam_lastlog.so inactive=` в `/etc/pam.d/common-auth`) не выполняется, даже если его включает `provisioning.disable_pam_lastlog_inactive`; воркеру уходит `skip_pam_fix=true`. От `preparation` не зависит (легаси `modes = False` снимал и PAM-правку, здесь это отдельный флаг стенда сценария). Хранится в `server_prepare_for_test_requests.skip_pam_fix`, возвращается в GET статуса.

CONTRACTS C2: тело `prepare-for-test` может нести `stand_setup` (`kernel_cmdline_extra`, `script`, `script_is_sensitive`, `run_as`, `phase`, `reboot_after`, `timeout_seconds`) и `provisioning` (`allowed_failed_units`, `degraded_reboot_attempts`, `disable_pam_lastlog_inactive`, `boot_wait_timeout_seconds`). Скрипт хранится зашифрованным (`stand_setup_script_encrypted`) и уходит воркеру через Redis-stash. Новые шаги `failed_step`: `pam_fix`, `stand_setup`; порядок — C2.

### `POST /internal/servers/{id}/stand-setup`

Auth: как у `prepare-for-test`. Тело: `correlation_id` (идемпотентность), `requested_by_department_id`, `test_username`, `stand_setup`, `provisioning`. Настройка уже подготовленного стенда без restore: `pam_fix` (по профилю), параметры ядра + `update-grub`, скрипт, перезагрузка и ожидание (задача воркера `server.stand_setup`). Бронь не берётся — её держит вызывающий. 202 `{stand_setup_request_id, status}`; исход — callback `{TESTING_SERVICE_URL}/internal/stand-setup/{id}/completed` (`correlation_id`, `succeeded`, `failed_step`, `error`). WARNING audit `server.stand_setup_requested`.

### `POST /internal/servers/{id}/stand-setup-done`

Auth: канал воркера, право `(server, prepare_callback)`. Тело: `stand_setup_request_id`, `succeeded`, `failed_step` (`pam_fix`/`stand_setup`/`reboot_verify`), `error`. WARNING audit `server.stand_setup_completed`.

Errors: `SERVER_NOT_FOUND`, `STAND_SETUP_REQUEST_NOT_FOUND` (404).

## Internal — ВМ-стенды для testing_service (CONTRACTS C4, hidden)

Зеркало серверного канала для ВМ: `src/api/v1/endpoints/internal_vm_service.py`, бронь — `src/services/vm_reservation.py`, подготовка — `src/services/vm_prepare_for_test.py`, выбор снимка — `src/services/vm_test_snapshots.py`. Auth всех `/internal/vms/{id}/…` ниже (кроме `-done`) — shared-secret + `X-Service-Identity: testing_service|acs`. Тела запросов — серверные; ответы — те же поля с `vm_id` вместо `server_id`.

Бронь ВМ хранится в `vms.service_busy_state` (`acs`/`testing`/`busy`/`testing_done`), `busy_service_name`, `busy_note`, `service_busy_since`; `vms.busy_state` — lifecycle-lock, в брони не участвует. Пока сервис держит ВМ, `vms.status = run test`. Во всех ответах бронь сведена к `busy_state` серверов: сервисная стадия → она сама; идёт операция над ВМ → `updating`; бронь человека (`status=<login>`) → `busy` (логин в `busy_note`); иначе `free`. Пока держится `acs`/`testing`/`busy`, человеческие операции над ВМ (reserve/release/status/power/delete/снимки/…) отбиваются 409 `VM_RESERVED_BY_SERVICE` — даже админу; консоль — нет.

### `POST /internal/vms/{id}/acquire-for-service`

Тело: как у сервера (`busy_state`, `busy_note`, `requested_by_department_id`, `takeover`). CAS: `status='free'`, нет lifecycle-операции, нет сервисной брони. `takeover=true` отнимает бронь человека и `testing_done` (`previous_holder`). INFO audit `vm.acquired_for_service`; takeover — WARNING `vm.reservation_taken_over`.

Errors: 401/403, `VM_NOT_FOUND` (404; и при чужом `requested_by_department_id`), `VM_ALREADY_BUSY` (409, `details.current_state`).

### `POST /internal/vms/{id}/release-for-service`, `…/release-for-service-as-done`, `…/service-status`

Как у сервера: снять свою бронь (→ `free`, `status=free`), перевести в `testing_done` (снимает человек `POST /vms/{id}/release` — любой с правом release), сменить стадию (`acs` → `testing`, `service_busy_since` не двигается). INFO audit `vm.released_for_service` / `vm.service_status_changed`.

Errors: `VM_NOT_FOUND` (404), `VM_NOT_BUSY` / `VM_RESERVED_BY_OTHER` (409).

### `GET /internal/vms/{id}/connection-info`

`{vm_id, host}` — IP гостя для SSH testing_worker'а. Errors: `VM_NOT_FOUND` (404), `VM_NO_IP_ADDRESS` (409).

### `GET /internal/vms/{id}/snapshots`

Замена `acs-snapshots`: снимки ВМ, пригодные для отката (не `_build`, `state=ready`), по имени, с разбором по шаблонам `/settings/vm-test`: `snapshot_id`, `name`, `kind`, `os_version`, `snapshot_mode`, `is_current`, `version_name` (версия из имени; `null` — имя не подошло ни к одному шаблону), `normalized_version` (`normalize_os_version_name`, по ней сравнивают), `mode` (из `{mode}` шаблона), `template`; плюс `templates` — действующий список. Чтение, audit не эмитится.

### `POST /internal/vms/{id}/prepare-for-test`

Тело: как у сервера, плюс `target` = `{type: "vm"}` (необязательно; `type=server` или чужой `vm_id` — 422 `PREPARE_TARGET_MISMATCH`; `target.type=vm` в серверном пути — тоже 422). `test_account_credential_id` для ВМ обязателен (иначе терминальный `failed_step=user_provision`, `TEST_ACCOUNT_NOT_CONFIGURED`). До похода на hub: версия и ядро по каталогу, ссылка на учётку, IP гостя, снимок по шаблонам (нормализованная версия, `{mode}` = режим запроса; несколько кандидатов — порядок шаблонов, точное совпадение, имя) — нет снимка → терминальный `failed`, `failed_step=vm_revert`, `VM_SNAPSHOT_NOT_FOUND: … (searched: …)`. Затем бронь (своя бронь вызывающего — только стадия `acs`; свободная ВМ — берём сами и снимаем на провале; иначе 409 `VM_ALREADY_BUSY`), Redis-stash (тестовая учётка + учётка входа в гостя + скрипт `stand_setup`) и задача воркера `vm.prepare_for_test`. 202 `{prepare_request_id, status}`; исход — тот же callback в `TESTING_SERVICE_URL` (без учётных данных). WARNING audit `server.prepare_for_test_requested` (`target_type=vm`).

Errors: `VM_NOT_FOUND` (404), `VM_ALREADY_BUSY` / `HUB_UNAVAILABLE` / `PREPARE_FOR_TEST_ALREADY_RUNNING` (409), `PREPARE_TARGET_MISMATCH` (422), `WORKER_*` (503).

### `GET /internal/vms/{id}/prepare-for-test/{prepare_request_id}`

Как у сервера; в ответе `vm_id`, `vm_snapshot_name`, `server_id=null`. Errors: `PREPARE_REQUEST_NOT_FOUND` (404).

### `POST /internal/vms/{id}/prepare-for-test-done`

Auth: канал воркера, право `(server, prepare_callback)` + `X-Target-Department-Id`. Тело: как у серверного `-done`; `failed_step` ∈ {`vm_revert`, `prepare`, `user_provision`, `pam_fix`, `stand_setup`, `kernel_change`, `mode_switch`, `reboot_verify`}. На провале снимает бронь, взятую самим запросом. WARNING audit `server.prepare_for_test_completed` (`target_type=vm`).

Errors: 403 (`TARGET_DEPARTMENT_HEADER_REQUIRED`), `VM_NOT_FOUND` / `PREPARE_REQUEST_NOT_FOUND` (404).

### `POST /internal/vms/{id}/stand-setup`

Тело: как у серверного `stand-setup`. Настройка подготовленной ВМ между ступенями многоступенчатого теста без отката снимка: `pam_fix` (по профилю), параметры ядра + `update-grub`, скрипт, перезагрузка и ожидание (задача воркера `vm.stand_setup`, тот же `run_setup_pipeline`). ВМ обязана быть под сервисной бронью вызывающего. Вход в гостя — учётка снимка последней успешной подготовки (иначе текущего снимка) → управляющие креды ВМ → базовая учётка образа; уезжает Redis-stash'ем вместе со скриптом. Строка — в `server_stand_setup_requests` (`vm_id`). 202 `{stand_setup_request_id, status}`; исход — тот же callback `{TESTING_SERVICE_URL}/internal/stand-setup/{id}/completed`. WARNING audit `server.stand_setup_requested` (`target_type=vm`).

Errors: `VM_NOT_FOUND` (404), `VM_NOT_BUSY` / `VM_RESERVED_BY_OTHER` / `HUB_UNAVAILABLE` / `VM_NO_IP_ADDRESS` (409).

### `POST /internal/vms/{id}/stand-setup-done`

Auth: канал воркера, право `(server, prepare_callback)` + `X-Target-Department-Id`. Тело: как у серверного `stand-setup-done`. WARNING audit `server.stand_setup_completed` (`target_type=vm`).

Errors: 403 (`TARGET_DEPARTMENT_HEADER_REQUIRED`), `VM_NOT_FOUND` / `STAND_SETUP_REQUEST_NOT_FOUND` (404).

### `GET /settings/vm-test`, `PUT /settings/vm-test`

Auth: Bearer + платформенный `account_admin` (как `/settings/probes`). `{snapshot_name_templates: [...]}` — шаблоны имени снимка ВМ, по порядку; плейсхолдеры `{version}` (ровно один раз), `{mode}`, `{hostname}` (hostname гостя, иначе имя ВМ), `{vm_name}`; до 10 шаблонов. Дефолт (миграция `a7d3e5c91b42`) — `["{version}", "{version}_{mode}"]`: легаси называл снимок ВМ версией (`allta_conf.json`, `cz_comm`), VM-домен — `<ver>_orel`/`<ver>_smolensk`. PUT заменяет список целиком. WARNING audit `settings.vm_test_updated`.

Errors: 401, `ACCOUNT_ADMIN_REQUIRED` (403), `VM_SNAPSHOT_TEMPLATE_INVALID` (422).

---

## Admin — master-key rotation (`/admin/encryption/*`)

UI-канал ротации мастер-ключа под платформенным `account_admin`. Ключи шифрования — инфраструктура, а не бизнес-данные, поэтому это явное исключение в `platform_admin_guard` (на остальных server_service-эндпоинтах account_admin отбивается 403). Ручки возвращают только статус ротации и версии ключа, без секретов. Зеркало ops-runner'ского канала, но action-name'ы отдельные (actor — человек из UI).

### `GET /admin/encryption/migration_status`

Auth: Bearer + платформенный `account_admin`. Read-only прогресс ре-шифрации секретов под активную версию (`remaining`/`total`/`by_version`/`migrated_pct`) — UI поллит после `rotate`, чтобы понять, когда можно `retire`.

Errors: `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED` (401), `ACCOUNT_ADMIN_REQUIRED` (403).

### `POST /admin/encryption/rotate`

Auth: Bearer + `account_admin`. Body: `{new_key_b64}` (ключ генерит auth_service). Новая версия становится активной, старые остаются читаемыми, фоновая ре-шифрация публикуется через reencrypt-outbox. Идемпотентно. CRITICAL audit `encryption.admin_rotate`.

Errors: `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED` (401), `ACCOUNT_ADMIN_REQUIRED` (403), `ROTATE_KEY_INVALID` (422).

### `POST /admin/encryption/retire/{version}`

Auth: Bearer + `account_admin`. Убирает старую версию из keystore (только осушённую и не активную). Дублирует автоматический авто-вывод (`encryption.auto_retire`): не-активную версию, на которой перешифровочный батч обнулил owner-строки и опустошил outbox, система выводит сама — ручной retire остаётся как резерв. CRITICAL audit `encryption.admin_retire`.

Errors: `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED` (401), `ACCOUNT_ADMIN_REQUIRED` (403), `KEYSTORE_CANNOT_RETIRE_ACTIVE` / `KEYSTORE_VERSION_IN_USE` (409).

---

## Уже задокументированный owner-trade-off

- **POST CREATE без Idempotency-Key.** `POST /servers`, `POST /server-accounts`, `POST /servers/{id}/ipmi`, `POST /os-versions` не читают header. Повтор полагается на UNIQUE — `409 *_DUPLICATE`. Owner-decision; внесение header'а здесь — отдельная задача.
- **`subject_type` для `/internal/*` не enforce'ится** — гейтит только матрица прав; user с расширенным grant'ом теоретически проходит. Owner-decision 2026-05-30.
- **Список envelope union (cursor vs offset) в OpenAPI не виден** — list-эндпоинты декларированы как `response_model=None`, чтобы FastAPI не строил дискриминированную схему. Trade-off: OpenAPI не описывает тип возврата напрямую; SDK-клиенты выбирают envelope по флагу `cursor`.

## Обнаружение ядер ОС

`POST /api/server/v1/os-versions/{id}/resolve-kernels` обновляет производный каталог `kernels` из настроенных репозиториев ОС. Доступен аутентифицированным читателям глобального каталога (включая сервисного бота testing_service); URL не принимаются от вызывающего. При отсутствии repo-строк используется существующий резолвер репозиториев по имени версии.

Читаются индексы `Packages.gz` с fallback на `Packages`, для строк sources.list — по компонентам в `binary-amd64`. Извлекаются версии пакетов `linux-image-…-generic` и `linux-image-…-lowlatency`, как в allta_app; meta/debug/headers пакеты исключены. Ядра дедуплицируются и сортируются по числовым компонентам. До трёх загрузок одновременно, общий срок 90 секунд, предел индекса 64 MiB. Сетевой сбой/битый индекс/отсутствие ядер не затирают сохранённые данные; изменение repo-строк во время запроса возвращает конфликт.
