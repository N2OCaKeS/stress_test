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

Поверх глобального `GLOBAL_RATE_LIMIT` (default 500/min per-IP, исключая `/health`/`/ready`) повешены per-endpoint лимиты (см. README §«Конфиг»):

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
| `RESOURCE_NOT_FOUND` | 404 | инстанс-ACL: ресурс (server/server_account) не найден / чужой отдел |
| `RESOURCE_PERMISSION_NOT_FOUND` | 404 | инстанс-ACL: revoke грантa, которого нет |
| `SERVER_DUPLICATE` / `IPMI_DUPLICATE` | 409 | UNIQUE на create |
| `OS_VERSION_DUPLICATE` | 409 | UNIQUE(name) на create/update os-версии |
| `RESOURCE_PERMISSION_ALREADY_EXISTS` | 409 | инстанс-ACL: гонка на UNIQUE при grant |
| `OS_VERSION_IN_USE` | 409 | delete os-версии, на которую ссылается сервер (FK RESTRICT) |
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
| `UNKNOWN_RESOURCE_TYPE` | 422 | resource_type вне `{server, server_account}` в `/resource-permissions/*` |
| `ACTION_NOT_INSTANCE_GRANTABLE` | 422 | инстанс-grant на глобально-только action (`create`, callback'и воркера, `view_management_credentials`, `manage_ignored_logins`) |
| `WEAK_PASSWORD` | 422 | пароль не прошёл политику |
| `PREPARE_REQUIRED` | 409 | inventory.sync / users.inventory / installed-packages на неподготовленный сервер (`is_managed=False`) — сначала prepare |
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

Auth: Bearer + `(server, *, create)`. Body: `ServerCreate` (hostname, ip_address, serial_number, department_id, optional storage / ipmi). `status` в теле нет — стартует `unknown`.

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

Auth: Bearer + `(server_account, *, rotate_password)`. Body: опц. `password_b64` (`base64.b64encode(plaintext)`, иначе `secrets.token_urlsafe(32)`; политика по декодированному plaintext). Меняет только ciphertext в БД, без SSH-apply. Plaintext НЕ возвращается. CRITICAL audit.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `WEAK_PASSWORD` (422), `RATE_LIMIT_EXCEEDED` (429).

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

Auth: Bearer + `(server_account, *, update)`. Body: `{ssh_mode, ssh_public_key?, ssh_private_key_b64?}`. `generate` — сервер генерит Ed25519 и возвращает приватный ключ ОДИН раз (`ssh_private_key`); `supply` — клиент передаёт `ssh_public_key` и опц. `ssh_private_key_b64` (если приложен — шифруется и хранится, тогда консоль сможет ходить под аккаунтом). Сохраняет ключ в БД и диспатчит `account.provision` на все серверы с `present_on_server=True` (push в `authorized_keys`). 202. WARNING audit `server_account.ssh_key_set`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404), `422` (`supply` без public-ключа / битый ключ).

### `POST /server-accounts/{account_id}/rotate_ssh_key` (worker dispatch)

Auth: Bearer + `(server_account, *, update)`. Перегенерирует Ed25519-пару (кейс компрометации), сохраняет public + зашифрованный private, возвращает новый приватный ключ ОДИН раз, диспатчит `account.provision` на все серверы с `present_on_server=True` (re-push authorized_keys). 202. CRITICAL audit `server_account.ssh_key_rotate`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` (404).

### `GET /server-accounts/{account_id}/ssh_private_key`

Auth: Bearer + `(server_account, *, view_password)` (то же право, что у раскрытия пароля). Расшифровывает и отдаёт приватный SSH-ключ в PEM. Доступен только для сгенерированных сервером ключей (`generate`/`rotate_ssh_key`); у `supply`-ключа без приватной части и у аккаунта без ключа → 404. Per-IP+account reveal-rate-limit (`PASSWORD_REVEAL_RATE_LIMIT`, default 10/min) поверх глобального. CRITICAL audit `server_account.ssh_private_key_revealed`.

Errors: `PERMISSION_DENIED` (403), `ACCOUNT_NOT_FOUND` / `ACCOUNT_NO_SSH_PRIVATE_KEY` (404), `DECRYPT_FAILED` (422), `RATE_LIMIT_EXCEEDED` (429).

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

Errors: `IDEMPOTENCY_KEY_TOO_LONG` (400), `PERMISSION_DENIED` (403), `SERVER_NOT_FOUND` (404), `SERVER_DECOMMISSIONED` / `PREPARE_REQUIRED` / `TASK_IDEMPOTENT_CONFLICT` / `IDEMPOTENCY_KEY_REUSE_CONFLICT` (409), `WORKER_REDIS_UNAVAILABLE` / `WORKER_UNREACHABLE` / `WORKER_REDIS_NOT_CONFIGURED` (503).

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

## Resource permissions (`/resource-permissions`)

Инстанс-уровневый ACL — точечные гранты роли на КОНКРЕТНЫЙ ресурс (`server` / `server_account`) поверх тип-wide матрицы `entity_permissions`. Слой аддитивный: эффективные права на ресурс = тип-wide объединение ∪ инстанс-гранты этого ресурса. Deny-грантов нет.

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

Auth: Bearer + `(permission, *, permission_grant)` либо account_admin. Выдать инстанс-грант `action` роли `role` на ресурс. Идемпотентно (повтор → возврат существующей строки без INSERT и без audit). Scope строки = отдел ресурса. Ответ — `ResourcePermissionResponse` (`{id, resource_type, resource_id, role, action, department_id, granted_by, created_at, updated_at}`). CRITICAL audit `resource_permission.grant`.

Errors: `PERMISSION_DENIED` (403), `RESOURCE_NOT_FOUND` (404 — ресурс не найден / чужой отдел), `RESOURCE_PERMISSION_ALREADY_EXISTS` (409 — гонка на UNIQUE), `UNKNOWN_RESOURCE_TYPE` / `ACTION_NOT_INSTANCE_GRANTABLE` (422).

### `DELETE /resource-permissions/{resource_type}/{resource_id}/{role}/{action}`

Auth: Bearer + `(permission, *, permission_revoke)` либо account_admin. Снять инстанс-грант. Ответ — `OkResponse`. CRITICAL audit `resource_permission.revoke`.

Errors: `PERMISSION_DENIED` (403), `RESOURCE_NOT_FOUND` (404 — ресурс не найден / чужой отдел) / `RESOURCE_PERMISSION_NOT_FOUND` (404 — такой строки нет), `UNKNOWN_RESOURCE_TYPE` (422).

### `POST /resource-permissions/{resource_type}/{source_resource_id}/propagate`

Auth: Bearer + `(permission, *, permission_grant)` (для `mode=mirror` — дополнительно `permission_revoke`) либо account_admin. Копирует ВСЕ инстанс-гранты ресурса-образца на список целей того же типа.

Body: `{"target_resource_ids": [...], "mode": "merge"|"mirror"}` (default `merge`; `target_resource_ids` — 1..500 элементов, сам образец из списка отбрасывается). `mode=merge` добавляет на каждую цель недостающие `(role, action)` образца, существующие не трогает; `mode=mirror` приводит цель к точной копии образца — добавляет недостающее И удаляет лишнее. Идемпотентно. Все цели обязаны быть в отделе образца, иначе `404 RESOURCE_NOT_FOUND`. CRITICAL audit `resource_permission.propagate`.

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

Header `X-Target-Department-Id` — опциональный в soft-режиме (default), обязательный при `INTERNAL_REQUIRE_DEPT_HEADER=true`. Несовпадение с реальным `server.department_id` → `403 TARGET_DEPARTMENT_MISMATCH`.

Общий набор кодов: `403 PERMISSION_DENIED` / `TARGET_DEPARTMENT_MISMATCH` / `TARGET_DEPARTMENT_HEADER_REQUIRED`, `404 SERVER_NOT_FOUND` / `ACCOUNT_NOT_FOUND` / `NO_IPMI_CONTROLLER`. Callback-routes дополнительно `422` (битый payload) и `500` `DECRYPT_FAILED` / `ENCRYPTION_KEY_MISSING`.

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
