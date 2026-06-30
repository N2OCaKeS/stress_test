# secret_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/secret/v1`.
> **Статус реализации:** Phase 1-7 + 9 + 10 закрыты; Phase 8 (integration) в работе. Подробности — `STATUS.md` (в этой же папке).
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
- **Bearer + `account_admin`** — `/admin/encryption/*` (ротация мастер-ключа из UI). Гейт — `require_account_admin`: пропускается только платформенный админ. Обычный CRUD/reveal для `account_admin` закрыт; здесь — инфраструктура (ключи), не бизнес-данные.
- **Internal bearer (auth_service)** — `/internal/lifecycle/*` принимают `Authorization: Bearer <SERVICE_API_KEY>` (или ключ из `SERVICE_API_KEYS`) **только** от identity `auth_service` (отдельный guard на `X-Service-Identity`; чужой caller — `401 WRONG_CALLER`).
- **Internal bearer (ops-runner)** — `/internal/migration_status`, `/internal/encryption/*`, `/internal/reencrypt_outbox/*` принимают любой валидный `SERVICE_API_KEY` / `SERVICE_API_KEYS` без жёсткой привязки caller'а (ротационный k8s-Job, воркер, account_admin через kubectl-exec).
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

`SLOWAPI_RATE_LIMIT` (default `120/second`) — глобальный per-IP лимит на всё, кроме `/health` и `/ready`. На `/reveal` отдельный per-actor throttle (5-мин окно).

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
**Response 200:** `CredentialList = { items: CredentialRead[], next_cursor: str | null }`. `CredentialRead` содержит метаданные кред'ы (`id`, `name`, `service`, `scope`, `owner_user_id`, `owner_dept_id`, `login`, `status`, `created_by`, `created_at`, `updated_at`, `blocked_at`, `blocked_reason`, `visible_to_dept`, `valid_from`, `valid_to`) — без plaintext-secret. Guest-роль получает `CredentialGuestList` — только `id`, `name`, `service`, `scope`, `visible_to_dept`; `valid_from` / `valid_to` гостю НЕ видны.
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
| `secret_b64` | str | да | base64(plaintext); клиент кодирует `base64.b64encode(plaintext)`. Декодированный plaintext — 1..8192 chars UTF-8, шифруется. Битый base64 / не-UTF-8 → `422`. |
| `owner_dept_id` | str \| null | для `department`/`cross_department` | Игнорируется для `personal` (owner = текущий user). |
| `valid_from` | datetime \| null | нет | UTC. Если задано — `reveal` до этого момента → `410 SECRET_NOT_YET_VALID`. |
| `valid_to` | datetime \| null | нет | UTC. Если задано — `reveal` после → `410 SECRET_EXPIRED`. Должен быть строго в будущем; `valid_to > valid_from`. |

**Response 201:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED` (нет права на create в данном scope/dep'е), `409 NAME_DUPLICATE`, `422 VALIDATION_ERROR` (несовместимость scope/owner_dept_id; битый base64 / не-UTF-8 / пустой или >8192 символов после декода — ловит pydantic-валидатор), `422 PLAINTEXT_TOO_LARGE` (>8192 байт — service-layer guard для multibyte-UTF-8 в пределах char-лимита), `422 ENCRYPT_INPUT_INVALID`.

### GET /credentials/{cred_id}

Метаданные кред'ы. Без plaintext.

**Auth:** Bearer.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `410 CREDENTIAL_BLOCKED`.

### PATCH /credentials/{cred_id}

Изменить `name` / `login` / `secret_b64`. На `secret_b64` — декод base64 → повторно шифрует.

**Auth:** Bearer (owner / dep_admin / admin secret_service своего dept'а — per scope).
**Body (`CredentialUpdate`):** все поля optional, partial update. `secret_b64` — base64(plaintext), как в create (опущен или null → секрет не меняется). Размеры — те же, что в create. Дополнительно `valid_from` / `valid_to` (datetime UTC) — позволяет admin'у продлить срок действия. NULL через PATCH не сбрасывает значение (если поле опущено — не трогаем); чтобы убрать окно — пересоздать креду.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 NAME_DUPLICATE`, `410 CREDENTIAL_BLOCKED`, `422 VALIDATION_ERROR`.

### DELETE /credentials/{cred_id}

Удалить credential. Admin override (не-owner через admin secret_service своего dept'а) требует `reason`. account_admin к delete НЕ допущен — управление содержимым секретов остаётся на dept-уровне.

**Auth:** Bearer (owner / dep_admin / admin secret_service своего dept'а).
**Body (`AdminDeleteRequest`, optional):** `{ "reason": "..." }` (1..256 chars). Обязателен для admin override, иначе `422 ADMIN_OVERRIDE_REASON_REQUIRED`.
**Response 200:** `OkResponse = { ok: true }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `422 ADMIN_OVERRIDE_REASON_REQUIRED`.

### POST /credentials/{cred_id}/reveal

Расшифровать secret и вернуть plaintext.

**Auth:** Bearer + `RoleACL.can_read` (или owner для personal).
**Body:** пусто.
**Response 200:** `{ "login": str | null, "secret_b64": "<base64(plaintext)>" }`.
**Throttle:** 5-минутное окно per `(actor_id, cred_id)`. Первый reveal — audit `tokens.revealed` **CRITICAL**, повторные в окне — `tokens.revealed_throttled` **INFO** с `count` в details. Окно скользящее.
**Validity window:** перед decrypt'ом проверяется `[valid_from, valid_to]` (UTC). До `valid_from` → `410 SECRET_NOT_YET_VALID` с `details.valid_from`. После `valid_to` → `410 SECRET_EXPIRED` с `details.valid_to`. В обоих случаях аудит `tokens.revealed_blocked_by_validity` (INFO, failure). Metadata-эндпоинты (`GET /credentials`, `GET /credentials/{id}`) НЕ блокируются — UI должен показать «продлите токен».
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `410 CREDENTIAL_BLOCKED`, `410 SECRET_NOT_YET_VALID`, `410 SECRET_EXPIRED`, `422 DECRYPT_FAILED` (ciphertext/AAD испорчены), `500 DECRYPT_INTERNAL_ERROR`, `503 ENCRYPTION_KEY_MISSING`.

### POST /credentials/{cred_id}/transfer

Передать ownership заблокированной кред'ы. Только для blocked кред.

**Auth:** Два пути входа. (1) Штатный — Bearer admin secret_service'а владеющего dept'а (per-(dept, service) роль): проходит endpoint-guard `require_transfer_recover_context` (для не-account_admin он делегирует `require_user_context` — нужен `secret_service` в `allowed_services` отдела), а внутри `credential_service.transfer` гейт `_is_service_admin_for` — admin того же dept'а, что владеет кред'ой. dep_admin чужого отдела по-прежнему отбивается `403 CREDENTIAL_ACCESS_DENIED`. (2) Emergency — Bearer account_admin: `require_transfer_recover_context` пропускает его несмотря на `department_id=null` и отсутствие dep-service-access, а ветка `_is_account_admin` в `credential_service.transfer` переназначает владельца. Нужен для кред'ы с удалённым владеющим отделом (у которого нет живого service-admin'а). Узкий путь: обычный CRUD/reveal account_admin'у недоступен. Audit несёт `actor_role` + `emergency_override`.
**Body (`TransferRequest`):** ровно одно из owner-полей + обязательный `reason`:

```json
{ "new_owner_user_id": "usr_...", "reason": "..." }
```
или
```json
{ "new_owner_dept_id": "dep_...", "reason": "..." }
```

`reason` (1..256 chars) обязателен — transfer считается CRITICAL-операцией; без него `422 VALIDATION_ERROR`. Ровно одно из `new_owner_user_id` / `new_owner_dept_id` (оба или ни одного → `422 VALIDATION_ERROR`). `personal` cred → `new_owner_user_id`, `department`/`cross_department` → `new_owner_dept_id`.

**Response 200:** `CredentialRead` (со снятым `status=blocked`).
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` (нет secret-access у dep'а; account_admin сюда не попадает — он проходит guard), `403 CREDENTIAL_ACCESS_DENIED` (не admin secret_service владеющего dept'а и не account_admin), `404 CREDENTIAL_NOT_FOUND`, `409 NAME_DUPLICATE` (active-дубль по target), `422 CREDENTIAL_NOT_BLOCKED`, `422 INVALID_TRANSFER_TARGET` (несоответствие scope), `422 VALIDATION_ERROR` (нет `reason` / не ровно один owner).

### POST /credentials/{cred_id}/recover

Снять `status=blocked` в окне `BLOCKED_RETENTION_DAYS` (default 30 дней от `blocked_at`).

**Auth:** Те же два пути, что у `/transfer`. Штатный — admin secret_service'а владеющего dep'а (`require_transfer_recover_context` → `require_user_context`, гейт `_is_service_admin_for`). Emergency — account_admin (guard пропускает, ветка `_is_account_admin` в `credential_service.recover` снимает блокировку в окне). Узкий путь — обычный CRUD/reveal account_admin'у недоступен. Audit `tokens.recover` несёт `actor_role` + `emergency_override`.
**Body:** пусто.
**Response 200:** `CredentialRead`.
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` (account_admin сюда не попадает), `403 CREDENTIAL_ACCESS_DENIED` (не admin secret_service владеющего dept'а и не account_admin), `404 CREDENTIAL_NOT_FOUND`, `409 NAME_DUPLICATE` (active-дубль), `422 CREDENTIAL_NOT_BLOCKED`, `422 RECOVER_WINDOW_EXPIRED`.

## RoleACL

ACL даёт видеть метаданные (`can_view`), читать значение (`can_read`) или менять (`can_write`) creds внутри одного департамента. Уровни вложены: `view ⊂ read ⊂ write`.

### POST /credentials/{cred_id}/acl

Выдать `RoleACL`.

**Auth:** owner-side выдача гейтится платформенной ролью `department_admin` (не service-роль `admin` — та даёт только read-override). Носитель `department_admin` владеющего dep'а проходит scope-проверку как `dept_admin`.
- `personal` cred: только owner (выдаёт в своём департаменте; `dept_id` должен равняться dept'у владельца, иначе `422 PERSONAL_ACL_OWNER_DEPT_ONLY`).
- `department` cred: `department_admin` владеющего dep'а.
- `cross_department` cred: `department_admin` recipient'а, **сначала** должен существовать `DeptGrant(cred_id, recipient_dept_id)`.

**Body (`RoleACLCreate`):**

| Поле | Тип | Обязательное |
|---|---|---|
| `dept_id` | str | да (1..64) |
| `role_name` | str | да (1..64). **Не валидируется** против каталога `auth.service_role_definitions` — принимается любая строка; несуществующая роль просто никогда не сматчит actor'а при access-check. |
| `can_view` | bool | default `false`. Видеть метаданные/листинг без значения (младший уровень лесенки `view ⊂ read ⊂ write`). |
| `can_read` | bool | default `false`. Reveal значения; влечёт `can_view`. |
| `can_write` | bool | default `false`. Изменение/удаление; влечёт `can_read` и `can_view`. |

**Response 201:** `RoleACLRead` (с `id`, `granted_by_user_id`, `granted_at`).
**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 ROLE_ACL_DUPLICATE`, `422 DEPT_GRANT_REQUIRED`, `422 PERSONAL_ACL_OWNER_DEPT_ONLY`.

> **Порядок проверок:** `grant_acl` сначала проходит `load_for_action` (visibility/access-check). Recipient dep_admin БЕЗ `DeptGrant` не видит cross-dep креду вовсе → отдаётся `404 CREDENTIAL_NOT_FOUND` (visibility-miss маскируется в 404, info-leak protection), а не `422 DEPT_GRANT_REQUIRED`. `422 DEPT_GRANT_REQUIRED` срабатывает позже — когда actor уже имеет доступ к кред'е, но пытается выдать ACL на ещё один dept без `DeptGrant` для него.

### PUT /credentials/{cred_id}/acl

Атомарный upsert `RoleACL`: задать желаемый набор `(can_view, can_read, can_write)` для `(dept_id, role_name)`. Идемпотентно, без `409` — заменяет связку `revoke`+`re-add` при тоггле ячейки матрицы.

- строки нет → создаётся (с `granted_by_user_id = actor`);
- строка есть → флаги переписываются (тот же `id`, `granted_at` сохраняется);
- все флаги `false` → строка удаляется («нет доступа» = отсутствие записи). Снятие несуществующей строки — идемпотентный no-op. Выдача только `can_view` (метаданные без значения) — валидная строка, не удаляется.

**Auth / scope-проверки:** те же, что у `POST` (гейт `grant_acl`, `personal` → owner-dep, cross-dep recipient → нужен `DeptGrant`).

**Body (`RoleACLUpsert`):** `dept_id` (str 1..64), `role_name` (str 1..64), `can_view` (bool, default `false`), `can_read` (bool, default `false`), `can_write` (bool, default `false`).

**Response 200 (`RoleACLUpsertResponse`):** `{ ok: true, acl: RoleACLRead | null }` — `acl` = `null`, когда все флаги сняты и строка удалена.

**Audit:** создание/обновление → `tokens.role_acl_added`; снятие (both-false) → `tokens.role_acl_revoked`.

**Error codes:** `401 UNAUTHORIZED`, `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `422 DEPT_GRANT_REQUIRED`, `422 PERSONAL_ACL_OWNER_DEPT_ONLY`.

### GET /credentials/{cred_id}/acl

Список `RoleACL`.

**Auth:** Bearer (owner / dep_admin владеющего dep'а / recipient dep_admin для cross_dep / admin secret_service владеющего dep'а).
**Response 200:** `RoleACLList = { items: RoleACLRead[] }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`.

### DELETE /credentials/{cred_id}/acl/{acl_id}

Revoke `RoleACL`.

**Auth:** Bearer (owner для personal / `department_admin` соответствующей стороны — owner-dep или recipient-dep). Гейт — action `grant_acl`; service-роль `admin` сюда НЕ проходит (она даёт только read-override).
**Response 200:** `OkResponse = { ok: true }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `404 ROLE_ACL_NOT_FOUND`.

## UserACL (поимённый доступ, только `personal`)

`CredentialUserACL` — поимённый слой доступа, разрешённый **только** для личных секретов (`scope=personal`). Владелец personal-кред'ы открывает доступ конкретному `user_id`. Для `department` / `cross_department` поимённый доступ запрещён — там доступ раздаётся исключительно ролями (`RoleACL`). Управление гейтится тем же action'ом `grant_acl`, что и `RoleACL`. Уровни — та же лесенка `view ⊂ read ⊂ write`: `can_view` (видеть наличие/метаданные), `can_read` (reveal значения, влечёт `can_view`), `can_write` (изменение/удаление, влечёт `can_read`). Младшие уровни подтягиваются автоматически при выдаче старшего.

### POST /credentials/{cred_id}/user-acl

Выдать поимённый доступ конкретному пользователю.

**Auth:** Bearer user (`require_user_context`). Гейт action `grant_acl` — владелец личного секрета. Bot/PAT без user-identity к personal-кред'е не допускается.
**Rate-limit:** отдельный `rate_limit_acl`.
**Body (`UserACLCreate`):**

| Поле | Тип | Обязательное | Примечание |
|---|---|---|---|
| `user_id` | str | да (1..64) | Кому выдаём (в UI выбирается по username, резолвится в `user_id` до вызова). Не себе и не владельцу. |
| `can_view` | bool | default `false` | Видеть наличие/метаданные без значения (младший уровень). |
| `can_read` | bool | **default `true`** | Reveal значения; влечёт `can_view`. Выдача без чтения смысла не имеет, но флаг явный ради симметрии с `RoleACL`. |
| `can_write` | bool | default `false` | Изменение/удаление; влечёт `can_read` и `can_view`. |

**Response 201:** `UserACLOut` (`id`, `cred_id`, `user_id`, `can_view`, `can_read`, `can_write`, `granted_by_user_id`, `created_at`).
**Audit:** `tokens.user_acl_added` (INFO; failure — ERROR).
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `409 USER_ACL_DUPLICATE`, `422 USER_ACL_SCOPE_NOT_PERSONAL` (cred не `personal`), `422 USER_ACL_OWNER_REDUNDANT` (target = владелец), `422 USER_ACL_SELF_REDUNDANT` (target = сам actor).

### GET /credentials/{cred_id}/user-acl

Список поимённых доступов кред'ы.

**Auth:** Bearer; доступ — как у `read` (load_for_action `read`).
**Response 200:** `UserACLList = { items: UserACLOut[] }`.
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`.

### DELETE /credentials/{cred_id}/user-acl/{acl_id}

Снять поимённый доступ.

**Auth:** Bearer; гейт action `grant_acl` (владелец личного секрета).
**Response 200:** `OkResponse = { ok: true }`.
**Audit:** `tokens.user_acl_removed` (INFO; failure — ERROR).
**Error codes:** `401 UNAUTHORIZED`, `403 CREDENTIAL_ACCESS_DENIED`, `404 CREDENTIAL_NOT_FOUND`, `404 USER_ACL_NOT_FOUND`.

## DeptGrant (только `cross_department`)

`DeptGrant` — это разрешение «recipient_dep_admin может выдавать `RoleACL` внутри своего dep'а на эту креду».

### POST /credentials/{cred_id}/dept-grants

Выдать `DeptGrant`.

**Auth:** Bearer `department_admin` владеющего dep'а. Service-роль `admin` secret_service'а сюда НЕ проходит — `grant_dept` гейтится в `access_service` только на платформенный `department_admin` (роль `admin` даёт лишь read-override). Bot без user-identity на personal/чужие cred'ы не допускается.
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

**Auth:** Bearer `department_admin` владеющего dep'а (как и выдача; `grant_dept` гейт — только `department_admin`, не service-роль `admin`).
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

## Ротация мастер-ключа — admin (UI, `account_admin`)

Ключи шифрования — инфраструктура, не бизнес-данные. Ротацию из UI инициирует платформенный `account_admin`; у него нет `department_id` и dep-service-access, поэтому штатный `require_user_context` отбил бы его `403`. Здесь гейт — `require_account_admin`, и только на эти три ручки. Обычный CRUD/reveal credentials для `account_admin` остаётся закрыт.

> **Двойственность ротации — осознанное решение.** `/admin/encryption/*` (человек из UI) и `/internal/encryption/*` (s2s ops-runner) вызывают одну и ту же бизнес-логику `key_rotation_service.rotate/.retire`, но различаются actor'ом и аудит-именем: `secrets.admin_encryption_*` для человека, `secrets.encryption_*` для скрипта. Разделение нужно, чтобы SIEM отличал ручную ротацию из UI от автоматической s2s-ротации.

### GET /admin/encryption/migration_status

Read-only прогресс ре-шифрации credentials под активную версию ключа (UI поллит после `rotate`).

**Auth:** Bearer + `require_account_admin`.
**Response 200:** `MigrationStatus` (`remaining_legacy`, `total_rows`, `by_version`, `migrated_pct`, `outbox_pending_count`). `retire` безопасен, когда `remaining_legacy == 0` и `outbox_pending_count == 0`.
**Error codes:** `401 UNAUTHORIZED`, `403 ACCOUNT_ADMIN_REQUIRED`.

### POST /admin/encryption/rotate

Рантайм-ротация мастер-ключа без простоя, из UI. Новая версия становится активной (новые токены сразу под ней), старые остаются читаемыми (материал в keystore), фоновая ре-шифрация публикуется через reencrypt-outbox. Идемпотентно: повтор с тем же материалом не плодит версию.

**Auth:** Bearer + `require_account_admin`.
**Body:** `{ "new_key_b64": "<base64(32 байта)>" }` (embed). Материал генерится через auth_service `POST /admin/service-keys/generate`.
**Response 200:** `RotateKeyResponse` (`new_version`, `previous_version`, `seeded`, `idempotent`).
**Audit:** `secrets.admin_encryption_rotate` (**CRITICAL**, actor = account_admin).
**Error codes:** `401 UNAUTHORIZED`, `403 ACCOUNT_ADMIN_REQUIRED`, `400 ROTATE_KEY_INVALID` (не base64 / не 32 байта).

### POST /admin/encryption/retire/{version}

Убрать старую версию мастер-ключа из keystore из UI. Разрешено только когда на версии 0 строк и она не активна.

**Auth:** Bearer + `require_account_admin`.
**Response 200:** `RetireKeyResponse` (`version`, `retired`).
**Audit:** `secrets.admin_encryption_retire` (**CRITICAL**, actor = account_admin).
**Error codes:** `401 UNAUTHORIZED`, `403 ACCOUNT_ADMIN_REQUIRED`, `409 KEYSTORE_CANNOT_RETIRE_ACTIVE`, `409 KEYSTORE_VERSION_IN_USE`.

## Ротация мастер-ключа — internal (s2s ops-runner)

Закрыты `require_internal_caller` (любой валидный `SERVICE_API_KEY` / `SERVICE_API_KEYS`, без жёсткой привязки caller'а). `include_in_schema=False`. Caller'ы: `rotation_runner` (k8s-Job из `rotate_secret_master_key.sh`), `worker` / `secret_worker`, либо `account_admin` через kubectl-exec.

### GET /internal/migration_status

Прогресс lazy re-encrypt'а под активную версию ключа (гейт для шага `--finalize` ротационного скрипта). Тот же payload, что у `/admin/encryption/migration_status`, отличается только auth.

**Auth:** Internal bearer (ops).
**Response 200:** `MigrationStatus`.
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`.

### POST /internal/encryption/rotate

Рантайм-ротация мастер-ключа по s2s-каналу. Эффект тот же, что у admin-rotate, инициатор — скрипт ротации.

**Auth:** Internal bearer (ops).
**Body:** `{ "new_key_b64": "<base64(32 байта)>" }` (embed).
**Response 200:** `RotateKeyResponse`.
**Audit:** `secrets.encryption_rotate` (**CRITICAL**, actor = service, в `details` — `caller`).
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `400 ROTATE_KEY_INVALID`.

### POST /internal/encryption/retire/{version}

Убрать старую версию мастер-ключа по s2s-каналу. Только когда на версии 0 строк и она не активна.

**Auth:** Internal bearer (ops).
**Response 200:** `RetireKeyResponse`.
**Audit:** `secrets.encryption_retire` (**CRITICAL**, actor = service).
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `409 KEYSTORE_CANNOT_RETIRE_ACTIVE`, `409 KEYSTORE_VERSION_IN_USE`.

## Reencrypt outbox (s2s ops-runner)

Outbox-паттерн поверх lazy-пути: lazy перешифровывает только читаемые кред'ы, а «холодные» (никто не reveal'ит) остались бы под старым ключом навсегда. Оператор / CronJob после ротации зовут `seed` → `process` → `status`. Все три закрыты `require_internal_caller`, `include_in_schema=False`, префикс `/internal/reencrypt_outbox`.

### POST /internal/reencrypt_outbox/seed

Просканировать credentials и опубликовать pending outbox-row'ы под целевую версию. Идемпотентно: partial UNIQUE по `credential_id WHERE status='pending'` не пустит дубль.

**Auth:** Internal bearer (ops).
**Body:** `{ "target_version": <int> | null }` (embed; null → `SECRET_ENCRYPTION_KEY_VERSION` из env).
**Response 200:** `ReencryptOutboxSeedResponse` (`inserted`, `scanned`, `active_version`).
**Audit:** `secrets.reencrypt_seed` (INFO, в `details` — `caller`).
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `500 REENCRYPT_OUTBOX_SEED_FAILED`.

### POST /internal/reencrypt_outbox/process

Обработать до `batch_size` pending-row'ов: decrypt legacy → encrypt active → CAS на `credentials.secret_encrypted`. После батча опустевшие неактивные версии ключа **выводятся автоматически** (`secrets.encryption_auto_retire`) — материал больше не нужен.

**Auth:** Internal bearer (ops).
**Query:** `batch_size` (default `100`, `1..1000`).
**Response 200:** `ReencryptOutboxProcessResponse` (`processed`, `errors`, `failed`).
**Audit:** `secrets.reencrypt_process` (INFO; если все строки упали — `processed==0 && errors>0` — failure/ERROR) + по факту авто-вывода `secrets.encryption_auto_retire` (**CRITICAL**, actor = service). Best-effort: сбой авто-retire не роняет обработку батча.
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`, `500 REENCRYPT_OUTBOX_PROCESS_FAILED`.

### GET /internal/reencrypt_outbox/status

Сводка outbox `{pending, done, error, total}`. Когда `pending == 0` и `error == 0` (и `migration_status.remaining_legacy == 0`) — старый мастер-ключ можно дропать.

**Auth:** Internal bearer (ops).
**Response 200:** `ReencryptOutboxStatus`.
**Error codes:** `401 INTERNAL_AUTH_REQUIRED`.

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
| `SECRET_NOT_YET_VALID` | 410 | Reveal вызван до `valid_from`. Detail: `{ "valid_from": "<iso UTC>" }`. Metadata-GET остаётся 200. |
| `SECRET_EXPIRED` | 410 | Reveal вызван после `valid_to`. Detail: `{ "valid_to": "<iso UTC>" }`. Metadata-GET остаётся 200. Sweep НЕ удаляет expired-кред'ы автоматически. |
| `CREDENTIAL_NOT_BLOCKED` | 422 | `transfer` / `recover` вызван для active cred'ы. |
| `NAME_DUPLICATE` | 409 | UNIQUE collision `(owner, service, name)` среди active. |
| `ADMIN_OVERRIDE_REASON_REQUIRED` | 422 | Admin override без `reason` в body. |
| `DEPT_GRANT_REQUIRED` | 422 | Создание ACL для cross_dep на dept без существующего `DeptGrant` — но только когда actor УЖЕ видит креду. Recipient без `DeptGrant` не видит её вовсе → получает `404 CREDENTIAL_NOT_FOUND` (visibility-miss маскируется в 404) РАНЬШЕ этой проверки. |
| `PERSONAL_ACL_OWNER_DEPT_ONLY` | 422 | ACL на personal-креду выдаётся с `dept_id != ` dept владельца. |
| `DEPT_GRANT_DUPLICATE` | 409 | UNIQUE collision `(cred_id, recipient_dept_id)`. |
| `DEPT_GRANT_NOT_APPLICABLE` | 422 | `DeptGrant` для не-cross_department кред'ы. |
| `DEPT_GRANT_RECIPIENT_IS_OWNER` | 422 | `recipient_dept_id == owner_dept_id`. |
| `DEPT_GRANT_NOT_FOUND` | 404 | Revoke по неизвестному `grant_id`. |
| `ROLE_ACL_DUPLICATE` | 409 | UNIQUE collision `(cred_id, dept_id, role_name)`. |
| `ROLE_ACL_NOT_FOUND` | 404 | Revoke по неизвестному `acl_id`. |
| `USER_ACL_DUPLICATE` | 409 | UNIQUE collision `(cred_id, user_id)` для поимённого доступа. |
| `USER_ACL_NOT_FOUND` | 404 | Revoke user-ACL по неизвестному `acl_id`. |
| `USER_ACL_SCOPE_NOT_PERSONAL` | 422 | Поимённый доступ выдан на не-`personal` креду. |
| `USER_ACL_OWNER_REDUNDANT` | 422 | Поимённый доступ выдан владельцу личного секрета (он и так допущен). |
| `USER_ACL_SELF_REDUNDANT` | 422 | Поимённый доступ выдан самому actor'у. |
| `INVALID_TRANSFER_TARGET` | 422 | `new_owner_user_id`/`new_owner_dept_id` не подходит под scope. |
| `RECOVER_WINDOW_EXPIRED` | 422 | `now - blocked_at > BLOCKED_RETENTION_DAYS`. |
| `DECRYPT_FAILED` | 422 | Ciphertext или AAD не сходятся (corruption / tamper). |
| `DECRYPT_INTERNAL_ERROR` | 500 | Внутренняя ошибка при decrypt'е. |
| `ENCRYPTION_KEY_MISSING` | 503 | `SECRET_ENCRYPTION_KEY__v<N>` для версии шифротекста не задан в env (legacy key не подгружен). |
| `ENCRYPT_INPUT_INVALID` | 422 | Plaintext пустой / не str. |
| `PLAINTEXT_TOO_LARGE` | 422 | Plaintext превышает `MAX_PLAINTEXT_BYTES`. |
| `ROTATE_KEY_INVALID` | 400 | `new_key_b64` не base64 / не 32 байта (admin/internal rotate). |
| `KEYSTORE_CANNOT_RETIRE_ACTIVE` | 409 | Попытка вывести активную версию мастер-ключа. |
| `KEYSTORE_VERSION_IN_USE` | 409 | На версии ещё остались строки — вывод запрещён. |
| `REENCRYPT_OUTBOX_SEED_FAILED` | 500 | Внутренний сбой `seed_outbox`. |
| `REENCRYPT_OUTBOX_PROCESS_FAILED` | 500 | Внутренний сбой `process_batch`. |
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
