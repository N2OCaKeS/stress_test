# auth_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/auth/v1`.
> **Всего endpoints:** **78** (auth 7, users 16, departments 4, services 3, service_roles 6, tokens 3, bots 9, groups 16, authorization 2, oauth2 5, docker 7).
> **Статус реализации:** production-ready (см. `../STATUS.md`).
> **Аудит-события:** перечислены в `AUDIT_EVENTS.md`.

## Общие правила

- Все эндпоинты только по HTTPS в prod (TLS-guard middleware).
- Access token короткоживущий (default 10 мин), refresh ротируется.
- PAT и bot-токены показываются один раз при создании, в БД — только хэш.
- Все значимые события идут в loging_service (см. `AUDIT_EVENTS.md`).
- Service-to-service endpoints (`/authorization/*`) закрыты `SERVICE_API_KEY` + опциональный `X-Service-Identity`.
- Login и `/docker/token` защищены lockout-механизмом: 5 неудач подряд → 15 минут блокировки (настраивается через `MAX_FAILED_LOGIN_ATTEMPTS` / `LOCKOUT_MINUTES`).

### Auth-режимы

- **Bearer (JWT / PAT / bot-token)** — `Authorization: Bearer <token>`.
- **Basic** — только `/docker/token` (`username:password`).
- **Service** — `Authorization: Bearer <SERVICE_API_KEY>` + опционально `X-Service-Identity` (обязателен, если задан `SERVICE_API_KEYS` JSON).
- **Public** — `/login`, `/refresh`, `/logout`, `/health`, `/ready`, `/docker/certs`, `/docker/jwks`, `/oauth2/token`.
- **`/oauth2/authorize`** — требует **Bearer user-JWT** (m2m `actor_type=oauth_client` отбивается `USER_CONTEXT_REQUIRED`).

### Формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
  "message": "Service 'config_service' is not allowed for this department",
  "details": { "service_name": "config_service" },
  "request_id": "req_123",
  "timestamp": "2026-04-18T12:00:00Z"
}
```

### Заголовки ответа

- `X-Request-ID` — корелляционный ID (генерируется middleware если не передан).
- `Retry-After` — на `429`.
- `WWW-Authenticate` — на `401` для bearer auth.
- `X-Total-Count` — полное число записей (без учёта `limit`/`offset`) на list-эндпоинтах с пагинацией.

### Пагинация

List-эндпоинты `GET /users`, `GET /users/department/{department_id}`, `GET /bots`, `GET /groups` принимают query-параметры:

| Параметр | Тип | Default | Диапазон |
|---|---|---|---|
| `limit` | int | `50` | `1..200` (вне диапазона → 422) |
| `offset` | int | `0` | `>= 0` (отрицательный → 422) |

Тело ответа остаётся плоским списком (`list[...]`); полное число записей под текущий scope/фильтр отдаётся в заголовке `X-Total-Count`. Для `GET /users/department/{department_id}` total считается в рамках отдела. Дефолтный `limit=50` отдаёт малые наборы целиком; для больших — клиент листает через `offset`.

### Lockout (защита от brute-force)

Общий pipeline через `services/_lockout.py`. Применяется к user-password (`POST /login`, `POST /users/me/password`, `GET /docker/token`), bot-token (`GET /docker/token` с `dbos_bot_…`) и OAuth client_secret (`POST /oauth2/token` с `grant_type=client_credentials`).

| Принципал | Счётчик | Порог (ENV / default) | Длительность (ENV / default) |
|---|---|---|---|
| User (`users`) | `failed_login_attempts` | `MAX_FAILED_LOGIN_ATTEMPTS` / `5` | `LOCKOUT_MINUTES` / `15` |
| Bot (`bot_accounts`) | `failed_token_attempts` | `BOT_MAX_FAILED_TOKEN_ATTEMPTS` / `5` | `BOT_LOCKOUT_MINUTES` / `15` |
| OAuth-client (`oauth_clients`) | `failed_secret_attempts` | `OAUTH_CLIENT_MAX_FAILED_SECRET_ATTEMPTS` / `5` | `OAUTH_CLIENT_LOCKOUT_MINUTES` / `15` |

Поведение:

- Каждая неудачная проверка секрета инкрементит соответствующий counter (атомарно).
- При достижении порога ставится `locked_until = now + <lockout_minutes>` соответствующей сущности.
- Все последующие попытки (для этого principal'а, любого секрета) → `429 ACCOUNT_TEMPORARILY_LOCKED` с полем `details.retry_after_seconds`.
- Успешная проверка сбрасывает счётчик и снимает блокировку.
- По истечении `locked_until` следующая попытка автоматически разлочивает principal'а (CAS-release).
- User-lockout общий между `/login`, `/users/me/password` (через `INVALID_OLD_PASSWORD`) и `/docker/token` (для password/PAT-варианта) — заблокированный аккаунт не пройдёт ни через один из них.

Lockout **не применяется** к refresh и PAT-токенам (там нет brute-force поверхности — токен либо валиден, либо нет; PAT на `/docker/token` идёт через user-lockout).

---

## Authentication (root)

### `POST /login`

Auth: public. Body:

```json
{ "username": "ivanov", "password": "secret" }
```

Response 200:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque>",
  "token_type": "Bearer",
  "expires_in": 600,
  "identity": { ... IdentityContext ... }
}
```

Errors: `INVALID_CREDENTIALS` (401), `USER_BANNED` (401), `ACCOUNT_TEMPORARILY_LOCKED` (429 + `retry_after_seconds`).

### `POST /refresh`

Auth: public (refresh — opaque secret). Body: `{ "refresh_token": "..." }`.

Response 200: `access_token`, `refresh_token` (новый), `token_type`, `expires_in`.

Errors:

- `REFRESH_TOKEN_INVALID` (401) — токен не найден, отозван, или ловля reuse → kill-switch на всю сессию (audit `token.refresh_reuse`).
- `REFRESH_TOKEN_EXPIRED` (401) — refresh истёк.
- `REFRESH_TOKEN_RACE` (401) — параллельный /refresh уже ротировал сессию (CAS-miss, benign race). Повтор с новым refresh.

### `POST /logout`

Auth: public. Body: `{ "refresh_token": "..." }` → `{ "ok": true }`. Идемпотент.

### `GET /me`

Auth: Bearer. Возвращает свежий `IdentityContext` (перечитывает из БД, не из JWT payload).

### `GET /health` / `GET /ready`

Auth: public. `{"status": "ok"|"ready", "service": "auth_service"}`.

### `POST /token`

OAuth2 Password flow для Swagger UI. `include_in_schema=False`. Логика — та же, что `/login`.

---

## Users (`/users`)

### `GET /users`

Auth: `account_admin`. Пагинация (`limit`/`offset`, см. общие правила). Response: `list[UserResponse]` + заголовок `X-Total-Count`.

### `GET /users/department/{department_id}`

Auth: AnyAdmin. `account_admin` — любой отдел; `department_admin` — только свой (иначе 404, чтобы не было ID oracle). Пагинация (`limit`/`offset`); `X-Total-Count` считается в рамках отдела.

Errors: `DEPARTMENT_NOT_FOUND` (404).

### `POST /users`

Auth: AnyAdmin. Body:

```json
{
  "username": "ivanov",
  "password": "<min 4 chars>",
  "email": "...",
  "department_id": "dep_xyz",
  "platform_role": "account_admin|department_admin|loging_admin|loging_reader|null",
  "initial_roles": [
    { "service_name": "config_service", "roles": ["reader", "operator"] }
  ]
}
```

`department_id` обязателен для всех, кроме `account_admin`. `department_admin` может создавать только в своём отделе и **не** account_admin. Назначать любую `platform_role` может только `account_admin`.

**`loging_reader` без `department_id` создаётся успешно** — `LOGING_READER` входит в `user_service._platform_admins` рядом с `account_admin` и `loging_admin`, поэтому auth_service не требует от него `department_id`. Политика «dept-scoped read» сейчас живёт только на стороне `loging_service`: на первом же GET аудита без `department_id` он отдаст `403 NO_DEPARTMENT`. Для глобального read-only по аудиту используется `platform_role=loging_admin` или `platform_role=account_admin`.

Errors: `USER_ALREADY_EXISTS` (409), `DEPARTMENT_NOT_FOUND` (404), `PERMISSION_DENIED` (403), `DEPARTMENT_ACCESS_DENIED` (403) — department_admin создаёт в чужом отделе, `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — не-account_admin пытается выдать `platform_role`.

### `PATCH /users/{user_id}`

Auth: AnyAdmin. Body (все поля опциональны): `email`, `department_id`, `status` (`UserStatus` enum), `platform_role` (`PlatformRole` enum).

Errors: `USER_NOT_FOUND` (404), `USER_UPDATE_FORBIDDEN` (403) — department_admin лезет в чужой отдел или меняет привилегированные поля, `STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN` (403) — не-account_admin меняет `status` на/с `BANNED`, `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — не-account_admin меняет `platform_role`.

### `POST /users/{user_id}/roles`

Auth: AnyAdmin. Replace-семантика для (user, service).

```json
{ "service_name": "config_service", "roles": ["reader", "operator"] }
```

Errors: `USER_NOT_FOUND` (404), `USER_INACTIVE` (409) — нельзя выдавать роль выключенному юзеру, `USER_ROLE_UPDATE_FORBIDDEN` (403) — department_admin лезет в чужой отдел, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — отдел не имеет access к сервису, `INVALID_SERVICE_ROLE` (422) — роль не определена для `(department, service)`.

### `POST /users/{user_id}/reset-password`

Auth: AnyAdmin. `account_admin` — любой юзер; `department_admin` — только свой отдел. Body: `{ "new_password": "<min 8>" }`.

Admin-вариант — для смены чужого пароля. Для self-reset используется `POST /users/me/password` (требует подтверждения старого пароля).

Errors: `USER_NOT_FOUND` (404), `USER_RESET_PASSWORD_FORBIDDEN` (403) — department_admin лезет в чужой отдел, `ACTOR_VANISHED` (401) — actor-юзер удалён между JWT-выдачей и вызовом.

### `POST /users/me/password`

Auth: Bearer (user-context, m2m отбивается). Body:

```json
{ "old_password": "...", "new_password": "<min 8, буквы + цифры>" }
```

Self-reset пароля с обязательным подтверждением `old_password`. После успеха — revoke всех активных сессий юзера (включая текущую), PAT остаются валидными. Audit `user.self_password_reset` (CRITICAL) с `caller_is_admin` в details для SIEM-фильтра по admin-self-reset'ам.

Errors: `INVALID_OLD_PASSWORD` (401, инкрементит lockout-счётчик), `SAME_PASSWORD` (422), `ACCOUNT_TEMPORARILY_LOCKED` (429 + `retry_after_seconds`), `USER_NOT_FOUND` (404).

### `GET /users/me/sessions`

Auth: Bearer (user-context, m2m отбивается). Response:

```json
{
  "items": [
    {
      "session_id": "ses_...",
      "created_at": "ISO-8601",
      "last_used_at": "ISO-8601 | null",
      "expires_at": "ISO-8601",
      "ip_address": "1.2.3.4 | null",
      "user_agent": "Mozilla/5.0 ... | null",
      "is_current": false
    }
  ],
  "total": 0
}
```

Список активных refresh-сессий юзера для UI «Active devices». `is_current=true` — это сессия, через `sid` claim которой был выдан access-токен текущего вызова. Истёкшие и revoked не возвращаются. Audit `user.sessions_listed` (INFO).

### `POST /users/me/sessions/revoke`

Auth: Bearer (user-context). Body:

```json
{ "except_current": false }
```

Отзывает все активные refresh-сессии юзера. `except_current=true` оставляет ту, через `sid` которой пришёл вызов (если в JWT нет `sid` — legacy-токен, сервер делает полный revoke). PAT и bot-токены не трогаются. Identity-cache инвалидируется. Response: `{"revoked_count": N}`. Audit `user.sessions_revoked_all` (CRITICAL).

### `DELETE /users/me/sessions/{session_id}`

Auth: Bearer (user-context). Целевой revoke одной своей сессии. Response: `{"revoked_count": 1}`. Audit `user.session_revoked_one` (WARNING).

Errors: `SESSION_NOT_FOUND` (404) — сессия не найдена, чужая или уже revoked (намеренно не отличаем, чтобы не было session-id-oracle между юзерами).

### `POST /users/{user_id}/ban`

Auth: `account_admin`. Body:

```json
{ "ban_type": "permanent|temporary", "reason": "...", "expires_at": "ISO-8601 или null" }
```

`temporary` требует `expires_at` в будущем. `permanent` — `expires_at` запрещён. Ban снимает все активные сессии + PAT + bot-токены owned-ботов.

Errors: `USER_NOT_FOUND` (404), 422 — cross-field инварианты.

### `POST /users/{user_id}/unban`

Auth: `account_admin`. Errors: `USER_NOT_FOUND` (404), `BAN_NOT_FOUND` (404).

### `GET /users/{user_id}/groups`

Auth: Bearer. `account_admin` — любой; `department_admin` — только свой отдел (cross-dept → 404); regular — только self.

Response: `list[UserGroupsResponse]`.

### `POST /users/{user_id}/groups`

Auth: AnyAdmin. Body: `{ "group_id": "..." }`.

Errors: `GROUP_NOT_FOUND` / `USER_NOT_FOUND` (404), `GROUP_DEPARTMENT_MISMATCH` (400), `ALREADY_GROUP_MEMBER` (409), `DEPARTMENT_ACCESS_DENIED` (403) — department_admin лезет в чужой отдел, `ROLE_REQUIRED` (403).

### `DELETE /users/{user_id}/groups/{group_id}`

Auth: AnyAdmin. Response: `{ "ok": true }`.

### `GET /users/{user_id}/permissions`

Auth: Bearer. `account_admin` — любой; `department_admin` — только свой отдел; сам юзер — себя; иначе 403.

Response (`UserPermissionsResponse`):

```json
{
  "user_id": "usr_abc",
  "username": "ivanov",
  "department_id": "dep_xyz",
  "department_name": "НТ",
  "platform_role": null,
  "is_active": true,
  "is_banned": false,
  "status": "ACTIVE",
  "direct_service_roles": [
    { "service_name": "config_service", "role_name": "reader", "assigned_at": "...", "assigned_by": "usr_admin" }
  ],
  "groups": [
    {
      "group_id": "grp_a",
      "group_name": "devs",
      "display_name": "Devs",
      "department_id": "dep_xyz",
      "joined_at": "...",
      "service_accesses": [{ "service_name": "config_service" }],
      "service_roles": [{ "service_name": "config_service", "role_name": "operator" }]
    }
  ],
  "allowed_services": ["config_service"],
  "service_roles": { "config_service": ["reader", "operator"] }
}
```

`direct_service_roles` + `groups` — raw слои (для UI с указанием источника). `allowed_services` + `service_roles` — effective view (INTERSECT). Для `account_admin` effective view пуст.

Errors: `USER_NOT_FOUND` (404), `PERMISSION_DENIED` (403).

---

## Departments (`/departments`)

### `GET /departments`

Auth: `account_admin`. Response: `list[DepartmentResponse]`.

### `POST /departments`

Auth: `account_admin`. Body: `{ "name": "...", "display_name": "..." }`.

Errors: `DEPARTMENT_ALREADY_EXISTS` (409).

### `POST /departments/{department_id}/services`

Auth: `account_admin`. Body: `{ "service_name": "config_service" }`. Создаёт `DepartmentServiceAccess`.

### `DELETE /departments/{department_id}/services/{service_name}`

Auth: `account_admin`. После revoke роли пользователей формально остаются, но эффективно отбрасываются `_merge_permissions` INTERSECT'ом.

---

## Platform services (`/services`)

### `GET /services` / `POST /services` / `DELETE /services/{service_name}`

Auth: `account_admin`.

`POST` body: `{ "service_name": "...", "display_name": "...", "description": "..." }`. После создания автоматически появляется системная роль `admin` (`is_system=True`).

Errors: `SERVICE_ALREADY_EXISTS` (409) на create, `SERVICE_NOT_FOUND` (404) на delete. На delete сервис не валится с 409 — каскадно деактивирует все `DepartmentServiceAccess`, `UserServiceRole`, `BotServiceRole` и `ServiceRoleDefinition` (audit `service.delete`, details `cascade_revoked_department_access=true`, `cascade_deactivated_roles=true`, `affected_bot_count`).

---

## Service roles (`/departments/{department_id}/services/{service_name}/roles`)

Управление ролями в scope `(department, service)`. Уникальность по тройке.

### `GET /` / `POST /` / `PATCH /{role_name}` / `DELETE /{role_name}`

Auth: Bearer. `account_admin` / `department_admin` своего отдела / юзер с access к сервису.

`POST` body: `{ "role_name": "...", "display_name": "...", "description": "..." }`.

`PATCH` body: `{ "display_name": "...", "description": "..." }` (опциональны).

Errors:
- `SERVICE_ROLE_ALREADY_EXISTS` (409) — уже есть в scope.
- `SERVICE_ROLE_MGMT_FORBIDDEN` (403) — нет прав управления ролями в `(department, service)`.
- `SERVICE_NOT_GRANTED_FOR_DEPARTMENT` (403) — у отдела нет access к сервису.
- `SERVICE_ROLE_NOT_FOUND` (404) на PATCH/DELETE неизвестной роли.
- `SERVICE_ROLE_SYSTEM_LOCKED` (403) на DELETE/PATCH системной роли (`is_system=True`).

### `POST /{role_name}/assign` / `POST /{role_name}/revoke`

Bulk-операции. Body: `{ "user_ids": ["usr_1", "usr_2"] }`. Idempotent. Юзеры из чужого отдела отбрасываются.

---

## Personal Access Tokens (`/tokens`)

Все операции — только для текущего юзера (`actor_id = identity.user_id`).

### `POST /tokens`

Auth: Bearer. Body:

```json
{
  "name": "cli-token",
  "expires_at": "2026-05-01T00:00:00Z",
  "allowed_services": ["config_service"]
}
```

Response (`PATCreateResponse`):

```json
{
  "token_id": "pat_123",
  "token": "dbos_pat_xxxxxxxxx",
  "name": "cli-token",
  "expires_at": "2026-05-01T00:00:00Z"
}
```

`token` показывается один раз. В БД — SHA-256 hash + prefix.

### `GET /tokens`

Auth: Bearer. Response: `list[PATListItem]` — без `token`/`token_hash`, только метаданные.

### `DELETE /tokens/{token_id}`

Auth: Bearer (только свой токен). Errors: `TOKEN_ALREADY_REVOKED` (409), `TOKEN_NOT_FOUND` (404).

---

## Bots (`/bots`)

### `POST /bots`

Auth: AnyAdmin. Body:

```json
{
  "name": "git_bot",
  "department_id": "dep_nt",
  "allowed_services": ["config_service"],
  "description": "bot for git automation"
}
```

Errors: `BOT_NAME_TAKEN` (409), `DEPARTMENT_NOT_FOUND` (404), `BOT_CREATION_FORBIDDEN` (403) — department_admin создаёт в чужом отделе, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — `allowed_services` содержит сервис, к которому отдел не подключён.

### `GET /bots`

Auth: AnyAdmin. `account_admin` — все; `department_admin` — только свой отдел. Пагинация (`limit`/`offset`, см. общие правила) + `X-Total-Count`.

### `PATCH /bots/{bot_id}`

Auth: AnyAdmin. Body (опциональны): `name`, `description`, `status` ("active"|"disabled"), `allowed_services`.

Errors: `BOT_NOT_FOUND` (404), `BOT_UPDATE_FORBIDDEN` (403) — department_admin лезет к боту чужого отдела, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — `allowed_services` содержит сервис, к которому отдел не подключён.

### `POST /bots/{bot_id}/tokens`

Auth: AnyAdmin. Body: `{ "name": "...", "expires_at": "ISO|null" }`.

Response:

```json
{
  "token_id": "bot_tok_123",
  "token": "dbos_bot_xxxxxxxxx",
  "name": "...",
  "expires_at": "..."
}
```

### `GET /bots/{bot_id}/tokens`

Auth: AnyAdmin. Response: `list[BotTokenListItem]` — только метаданные.

### `DELETE /bots/{bot_id}/tokens/{token_id}`

Auth: AnyAdmin. Response: `{ "ok": true }`.

### `GET /bots/{bot_id}/roles`

Auth: AnyAdmin. Response: `list[BotRoleResponse]` (по service_name).

### `POST /bots/{bot_id}/roles`

Auth: AnyAdmin. Replace-семантика для (bot, service). Body:

```json
{ "service_name": "config_service", "roles": ["operator"] }
```

Errors: `BOT_NOT_FOUND` (404), `BOT_INACTIVE` (409) — выключенный бот, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — отдел не имеет access, `SERVICE_NOT_IN_BOT_ALLOWED` (403) — сервис не в `bot.allowed_services`, `INVALID_SERVICE_ROLE` (422) — роль не определена в `(department, service)`, `BOT_ROLE_MGMT_FORBIDDEN` (403) — department_admin лезет к чужому боту.

### `DELETE /bots/{bot_id}/roles/{service_name}`

Auth: AnyAdmin. Снимает все `BotServiceRole` для пары (bot, service).

---

## Groups (`/groups`)

### `GET /groups`

Auth: Bearer. `account_admin` — все; `department_admin` / regular — только свой scope. Пагинация (`limit`/`offset`, см. общие правила) + `X-Total-Count`.

### `POST /groups`

Auth: Bearer (account_admin или department_admin своего отдела). Body:

```json
{ "department_id": "dep_xyz", "name": "devs", "display_name": "Devs", "description": "..." }
```

Errors: `GROUP_ALREADY_EXISTS` (409) — имя занято в отделе; `DEPARTMENT_ACCESS_DENIED` (403) — cross-dept у department_admin; `DEPARTMENT_NOT_FOUND` (404).

### `PATCH /groups/{group_id}`

Auth: Bearer (admin своего отдела). Body: `display_name`, `description` (опциональны).

### `DELETE /groups/{group_id}`

Auth: Bearer. Каскадно убирает members + group_service_roles/access.

### `GET /groups/{group_id}/members`

Auth: Bearer. account_admin / department_admin своего отдела / любой member группы.

### `POST /groups/{group_id}/members`

Auth: AnyAdmin. Body: `{ "user_id": "..." }`. Юзер и группа должны быть в одном отделе.

Errors: `GROUP_DEPARTMENT_MISMATCH` (400), `GROUP_NOT_FOUND` / `USER_NOT_FOUND` (404).

### `DELETE /groups/{group_id}/members/{user_id}`

Auth: AnyAdmin.

### `GET /groups/{group_id}/bots`

Auth: Bearer. account_admin / department_admin своего отдела. Response: `list[BotMemberResponse]`.

### `POST /groups/{group_id}/bots`

Auth: AnyAdmin. Body: `{ "bot_id": "..." }`. Бот и группа должны быть в одном отделе. Бот наследует service-роли группы (∩ `bot.allowed_services`).

Errors: `GROUP_DEPARTMENT_MISMATCH` (400), `GROUP_NOT_FOUND` / `BOT_NOT_FOUND` (404), `ALREADY_GROUP_MEMBER` (409).

### `DELETE /groups/{group_id}/bots/{bot_id}`

Auth: AnyAdmin. Errors: `MEMBER_NOT_FOUND` (404).

### `GET /groups/{group_id}/services`

Auth: Bearer. account_admin / department_admin своего отдела / member группы. Response: `list[GroupServiceAccessResponse]`.

### `POST /groups/{group_id}/services`

Auth: Bearer (admin своего отдела). Body: `{ "service_name": "config_service" }`. Сервис должен быть в `allowed_services` отдела (иначе `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`). Также эмиттится `GROUP_SERVICE_ALREADY_GRANTED` (409) при повторном grant'е.

### `DELETE /groups/{group_id}/services/{service_name}`

Auth: Bearer. После revoke group_service_roles отбрасываются на INTERSECT.

### `GET /groups/{group_id}/roles`

Auth: Bearer. account_admin / department_admin своего отдела / member группы.

### `POST /groups/{group_id}/roles`

Auth: Bearer (admin своего отдела). Replace-семантика. Body:

```json
{ "service_name": "config_service", "roles": ["operator"] }
```

Errors: `GROUP_SERVICE_ACCESS_REQUIRED` (403) — нет group_service_access; `SERVICE_NOT_FOUND` (404); `INVALID_SERVICE_ROLE` (422) — роль не определена в `(department, service)`.

### `DELETE /groups/{group_id}/roles/{service_name}`

Auth: Bearer. Снимает все `GroupServiceRole` для пары.

---

## Authorization (`/authorization`)

Service-to-service. Закрыто `SERVICE_API_KEY` + опциональный `X-Service-Identity` (валидируется по `KNOWN_SERVICE_IDENTITIES` или, если задан `SERVICE_API_KEYS` JSON env, по per-service ключу).

### `POST /authorization/introspect`

Body: `{ "token": "<jwt|pat|bot>" }`.

Response (`IntrospectResponse`):

```json
{
  "active": true,
  "subject_type": "user|bot|oauth_client",
  "sub": "usr_abc",
  "username": "ivanov",
  "department_id": "dep_xyz",
  "department_name": "НТ",
  "platform_role": null,
  "is_banned": false,
  "allowed_services": ["config_service"],
  "service_roles": { "config_service": ["reader"] },
  "exp": 1770000000
}
```

`active=false` при невалидном/истёкшем/отозванном токене (остальные поля None / пустые). Чувствительные поля (`is_banned`, `allowed_services`, `service_roles`) — свежее чтение из БД, не из JWT payload.

Для всех трёх типов субъектов `allowed_services`/`service_roles` собираются единым путём (`collect_user_permissions`) — учитываются как прямой dept-access, так и сервисы, доступные юзеру через группы. PAT даёт пересечение собственного scope (`allowed_services`) с реально доступным юзеру набором (dept ∪ группы), симметрично user-JWT и `/me`; права PAT при этом не расширяются.

Errors: 401 при отсутствии/неверном `SERVICE_API_KEY` или `X-Service-Identity` (в strict-режиме); 429 при rate-limit.

### `POST /authorization/service-access`

Body:

```json
{ "subject_token": "<token>", "service_name": "config_service" }
```

Response: `{ "allowed": true|false, "department_id": "...", "service_roles": ["reader"] }`.

---

## OAuth2 (`/oauth2`)

### `POST /oauth2/clients`

Auth: AnyAdmin. `account_admin` — любой отдел; `department_admin` — только свой. Body:

```json
{
  "name": "external_app",
  "description": "...",
  "department_id": "dep_xyz",
  "redirect_uris": ["https://app.example.com/callback"],
  "allowed_scopes": ["openid", "profile"],
  "grant_types": ["authorization_code", "client_credentials"]
}
```

`redirect_uris` — только https (или `http://localhost[:port]` для нативных клиентов), без fragment (`#`).

Response (`OAuthClientCreatedResponse`):

```json
{
  "id": "...",
  "client_id": "...",
  "client_secret": "cs_...",
  "department_id": "...",
  "name": "...",
  "redirect_uris": [...],
  "allowed_scopes": [...],
  "grant_types": [...],
  "is_active": true,
  "created_at": "..."
}
```

`client_secret` показывается один раз.

Errors: `OAUTH_CLIENT_NAME_EXISTS` (409), `DEPARTMENT_NOT_FOUND` (404), `DEPARTMENT_ACCESS_DENIED` (403), `redirect_uri_*` (422).

### `GET /oauth2/clients`

Auth: AnyAdmin. Query: `department_id` (опц.). Response: `list[OAuthClientResponse]` без `client_secret`.

### `DELETE /oauth2/clients/{client_id}`

Auth: AnyAdmin. Каскад: убивает все authorization codes и issued токены клиента.

### `GET /oauth2/authorize`

Auth: Bearer (user JWT; `actor_type=oauth_client` отбивается `require_user_context`). Query params:

- `client_id` (обязателен)
- `redirect_uri` (обязателен; должен быть в whitelist'е клиента)
- `response_type` (default `code`; только `code` разрешён)
- `scope` (опц., space-separated)
- `state` (опц.)
- `code_challenge`, `code_challenge_method` (PKCE; опц. для confidential, обязателен для public)
- `state` — макс. длина **2048** (max_length=2048), иначе 422.

Response: 302 redirect на `{redirect_uri}?code=…&state=…`.

Errors: `UNSUPPORTED_RESPONSE_TYPE` (400), `REDIRECT_URI_MISMATCH` (403) — uri не в whitelist'е клиента, `OAUTH_CLIENT_INVALID` (401) — нет такого client_id или клиент деактивирован, `GRANT_TYPE_NOT_ALLOWED` (403) — `authorization_code` не в `grant_types` клиента, `PKCE_REQUIRED` (403) — для public-клиента, `PKCE_METHOD_INVALID` (403) — public требует `S256`, confidential — `S256`/`plain`, `USER_CONTEXT_REQUIRED` (403) — m2m-токен на user-endpoint.

**`state` — обязанности клиента.** Параметр `state` сервер прозрачно прокидывает обратно в `redirect_uri` без интерпретации (RFC 6749 §10.12). Защита от CSRF на этом канале — на стороне клиента: клиент **обязан** генерировать криптостойкий `state` (например `secrets.token_urlsafe(32)`), привязывать его к сессии (cookie/session storage) и при колбэке проверять равенство `state`-присланного и сохранённого. Сервер ограничивает длину 2048 символами, но не валидирует содержимое и не помнит выданные значения.

**PKCE рекомендуется всегда.** Для public-клиентов (без `client_secret`, флаг `is_public`) PKCE с `code_challenge_method=S256` обязателен (`PKCE_METHOD_INVALID` 400 при отсутствии). Для confidential-клиентов сервер допускает запрос без PKCE, но рекомендуется его всё равно использовать — это закрывает класс атак на перехват `code` в логах, прокси и истории браузера. `plain` оставлен для совместимости и должен не использоваться в новых интеграциях.

### `POST /oauth2/token`

Auth: public. Body:

```json
{
  "grant_type": "authorization_code|refresh_token|client_credentials",
  "code": "...",
  "redirect_uri": "...",
  "client_id": "...",
  "client_secret": "...",
  "code_verifier": "..."
}
```

Pydantic Literal допускает все три значения; на runtime реализованы только `authorization_code` (с PKCE) и `client_credentials`. `refresh_token` → `UNSUPPORTED_GRANT_TYPE`.

Response (`OAuthTokenResponse`): `access_token`, `token_type=Bearer`, `expires_in`, `scope`.

Errors: `OAUTH_CODE_INVALID` (401) — код не найден / уже использован; `OAUTH_CODE_EXPIRED` (401); `REDIRECT_URI_MISMATCH` (403) — redirect_uri не совпал с тем, под которым код был выдан; `INVALID_GRANT` (400) — PKCE verifier не совпал / code-row помечен use'нутым между check и mark; `OAUTH_CLIENT_INVALID` (401) — неверный `client_id`/`client_secret`; `GRANT_TYPE_NOT_ALLOWED` (403) — grant не в `client.grant_types`; `UNSUPPORTED_GRANT_TYPE` (400/422); `OAUTH_USER_NOT_FOUND` / `OAUTH_USER_INACTIVE` (401) — owner кода удалён / деактивирован.

---

## Docker registry (`/docker`)

### `PUT /docker/registry/{department_id}`

Auth: AnyAdmin (account_admin или department_admin своего отдела). Replace-семантика. Body:

```json
{
  "pull_policy": "all|restricted",
  "pull_user_ids": ["usr_1"],
  "push_user_ids": ["usr_1"]
}
```

`pull_user_ids` обязателен для `restricted`. Response: `DockerRegistryConfigResponse`.

Errors: `DEPARTMENT_NOT_FOUND` (404), `DEPARTMENT_ACCESS_DENIED` (403) — DA лезет в чужой отдел, `PULL_USERS_REQUIRED` (403) — `pull_policy=restricted` без `pull_user_ids`.

### `PATCH /docker/registry/{department_id}`

Auth: AnyAdmin. Частичный update. Body — те же поля + `is_enabled` (bool).

Errors: `DOCKER_REGISTRY_NOT_CONFIGURED` (404) — конфиг не создан, `DEPARTMENT_ACCESS_DENIED` (403), `PULL_USERS_REQUIRED` (403).

### `GET /docker/registry/{department_id}`

Auth: AnyAdmin. Response: `DockerRegistryConfigResponse`. `DOCKER_REGISTRY_NOT_CONFIGURED` (404) если конфиг не создан.

### `DELETE /docker/registry/{department_id}`

Auth: AnyAdmin. Снимает конфиг полностью. Errors: `DOCKER_REGISTRY_NOT_CONFIGURED` (404).

### `GET /docker/token`

Auth: Basic (`username:password`). Query: `service`, `scope`, `account` (per Docker token-auth protocol).

Варианты пароля:
- `username:<user password>` — обычные кредлы юзера;
- `username:dbos_pat_…` — PAT как пароль;
- `botname:dbos_bot_…` — bot-токен.

Lockout-pipeline идентичен `/login`: 5 неудач → 15 мин блокировки.

Response (`DockerTokenResponse`):

```json
{
  "token": "<RS256 JWT>",
  "access_token": "<same as token>",
  "expires_in": 300,
  "issued_at": "2026-04-18T12:00:00Z"
}
```

Errors:
- `MISSING_CREDENTIALS` (401) — нет Basic-заголовка.
- `INVALID_CREDENTIALS` (401) — пароль не подошёл.
- `ACCOUNT_TEMPORARILY_LOCKED` (429 + `retry_after_seconds`) — lockout активен.
- `DOCKER_ACCESS_DENIED` (403) — нет конфига registry для отдела или отключён.

### `GET /docker/certs`

Auth: public. Response: PEM-encoded RSA public key (для registry `rootcertbundle`).

### `GET /docker/jwks`

Auth: public. Response: JWKS (RS256).

---

## Каталог error_codes

Только коды, реально поднимаемые `auth_service`. Источник — `grep error_code= src/` (исключения `core/exceptions.py` и raise-сайты сервисов).

### Валидация / формат запроса

- `MISSING_REQUIRED_FIELD` (400) — обязательное поле отсутствует (например `department_id` для обычного юзера).
- `UNSUPPORTED_GRANT_TYPE` (400/422) — grant_type не из `authorization_code` / `client_credentials`.
- `UNSUPPORTED_RESPONSE_TYPE` (400) — `response_type` отличается от `code`.
- `INVALID_TOKEN_EXPIRY` (422) — `expires_at` в прошлом / битый формат.
- Pydantic 422: `redirect_uri_invalid`, `redirect_uri_has_fragment`, `redirect_uri_not_https`, `redirect_uri_scheme_invalid` — валидация `redirect_uris` на регистрации OAuth-клиента.

### Логин и сессии

- `INVALID_CREDENTIALS` (401) — пароль не подошёл / нет такого юзера.
- `USER_BANNED` (401) — юзер забанен.
- `USER_BLOCKED` (401) — `user.status=BLOCKED`.
- `INVALID_OLD_PASSWORD` (401) — `POST /users/me/password` — старый пароль не подошёл (инкрементит lockout-счётчик).
- `SAME_PASSWORD` (422) — `new_password` совпадает с текущим.
- `SESSION_NOT_FOUND` (404) — `DELETE /users/me/sessions/{id}`.
- `REFRESH_TOKEN_INVALID` (401) — токен не найден / отозван / reuse-detection (kill-switch).
- `REFRESH_TOKEN_EXPIRED` (401).
- `REFRESH_TOKEN_RACE` (401) — параллельный refresh выиграл CAS, повтор с новым refresh.
- `ACCESS_TOKEN_EXPIRED` (401) — access JWT с `exp < now` (с учётом `JWT_LEEWAY_SECONDS`). Клиенту допустимо запускать refresh-flow.
- `INVALID_TOKEN` (401) — отсутствующий / structurally invalid / bad-signature / unknown actor_type / missing claims. Refresh не поможет; нужен повторный login.

### Доступ и авторизация

- `PERMISSION_DENIED` (403) — общий fallback на endpoint'е без своего кода.
- `ROLE_REQUIRED` (403) — нужна определённая платформенная роль.
- `DEPARTMENT_ACCESS_DENIED` (403) — DA лезет в чужой отдел (общий код для всех cross-dept нарушений).
- `USER_CONTEXT_REQUIRED` (403) — m2m identity (`actor_type=oauth_client`) на user-facing endpoint'е.
- `STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN` (403) — не-account_admin меняет `status` ↔ `BANNED` через PATCH.
- `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — не-account_admin выдаёт `platform_role`.
- `ACTOR_VANISHED` (401) — actor-юзер удалён между JWT-выдачей и вызовом.

### Service-to-service auth

- `INVALID_SERVICE_TOKEN` (401) — `SERVICE_API_KEY` отсутствует / неверный / per-service mismatch.
- `MISSING_SERVICE_IDENTITY` (401) — header `X-Service-Identity` обязателен, но отсутствует (strict mode либо `SERVICE_API_KEYS` JSON задан).
- `INVALID_SERVICE_IDENTITY` (401) — identity не в `KNOWN_SERVICE_IDENTITIES` (strict mode).

### Cross-service ссылки

- `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (403) — поднимает `server_service` middleware'ом для `account_admin`/`loging_admin` ДО endpoint-логики. В `auth_service` не эмитится, но клиенты должны его знать (request к server_service через user-JWT).

### PAT (Personal Access Tokens)

- `TOKEN_NOT_FOUND` (404) — токен не найден / не твой (oracle защищён 404'ом).
- `TOKEN_ALREADY_REVOKED` (409) — уже revoked.
- `TOKEN_NAME_ALREADY_EXISTS` (409) — имя занято для текущего юзера.
- `INVALID_TOKEN_EXPIRY` (422).

### Bot / service account

- `BOT_NOT_FOUND` (404), `BOT_NAME_TAKEN` (409) — глобально-уникальное имя бота.
- `BOT_INACTIVE` (409) — попытка выдать роль/добавить в группу выключенного бота.
- `BOT_TOKEN_NOT_FOUND` (404), `BOT_TOKEN_ALREADY_REVOKED` (409).
- `BOT_CREATION_FORBIDDEN` (403) — DA создаёт бота в чужом отделе.
- `BOT_UPDATE_FORBIDDEN` (403) — DA PATCH'ит чужого бота (с `bot.update` failure-audit).
- `BOT_ROLE_MGMT_FORBIDDEN` (403) — DA управляет ролями чужого бота.
- `SERVICE_NOT_IN_BOT_ALLOWED` (403) — сервис не в `bot.allowed_services`.

### Users / admin

- `USER_NOT_FOUND` (404).
- `USER_ALREADY_EXISTS` (409) — `username` занят на `POST /users`.
- `USER_UPDATE_FORBIDDEN` (403) — DA PATCH'ит чужого юзера.
- `USER_ROLE_UPDATE_FORBIDDEN` (403) — DA назначает роли юзеру чужого отдела.
- `USER_RESET_PASSWORD_FORBIDDEN` (403) — DA сбрасывает пароль юзеру чужого отдела.
- `USER_INACTIVE` (409) — выдать роль выключенному юзеру.
- `USER_DEPARTMENT_MISMATCH` (400) — внутреннее несоответствие (юзер vs scope).
- `USER_BANNED_OR_INACTIVE` (401) — попытка использовать токен забаненного юзера в introspect.
- `BAN_ALREADY_ACTIVE` (409), `BAN_NOT_FOUND` (404).

### Groups

- `GROUP_NOT_FOUND` (404).
- `GROUP_ALREADY_EXISTS` (409) — имя занято в отделе.
- `GROUP_DEPARTMENT_MISMATCH` (400) — юзер/бот не из того же отдела.
- `GROUP_SERVICE_ACCESS_REQUIRED` (403) — нет `GroupServiceAccess` для целевого сервиса.
- `GROUP_SERVICE_NOT_FOUND` (404), `GROUP_SERVICE_ALREADY_GRANTED` (409).
- `MEMBER_NOT_FOUND` (404), `ALREADY_GROUP_MEMBER` (409).

### Service roles

- `SERVICE_ROLE_ALREADY_EXISTS` (409) — определение роли уже в `(department, service)`.
- `SERVICE_ROLE_NOT_FOUND` (404).
- `SERVICE_ROLE_SYSTEM_LOCKED` (403) — системную (`is_system=True`) роль нельзя менять / удалять.
- `SERVICE_ROLE_MGMT_FORBIDDEN` (403) — нет прав управления каталогом ролей в scope.
- `INVALID_SERVICE_ROLE` (422) — на assign передана роль, которой нет в каталоге.
- `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` (403) — у отдела нет access к сервису.
- `SERVICE_NOT_GRANTED_FOR_DEPARTMENT` (403) — симметричный код для управления service-role catalog'ом.

### OAuth2

- `OAUTH_CLIENT_INVALID` (401) — неверный `client_id`/`client_secret` или клиент деактивирован.
- `OAUTH_CLIENT_NOT_FOUND` (404).
- `OAUTH_CLIENT_NAME_EXISTS` (409).
- `OAUTH_CODE_INVALID` (401), `OAUTH_CODE_EXPIRED` (401).
- `OAUTH_USER_NOT_FOUND` (401), `OAUTH_USER_INACTIVE` (401) — owner кода удалён / деактивирован между issue и exchange.
- `REDIRECT_URI_MISMATCH` (403) — uri не в whitelist'е либо не совпал с тем, под которым код выдан.
- `GRANT_TYPE_NOT_ALLOWED` (403) — grant не в `client.grant_types`.
- `INVALID_GRANT` (400) — PKCE verifier mismatch / code-row marked used между check и mark.
- `PKCE_REQUIRED` (403) — public client без `code_challenge`.
- `PKCE_METHOD_INVALID` (403) — public требует `S256`, confidential — `S256`/`plain`.

### Docker

- `DOCKER_ACCESS_DENIED` (403) — нет конфига registry для отдела или registry disabled.
- `DOCKER_REGISTRY_NOT_CONFIGURED` (404).
- `MISSING_CREDENTIALS` (401) — нет Basic-заголовка на `/docker/token`.
- `PULL_USERS_REQUIRED` (403) — `pull_policy=restricted` без `pull_user_ids`.
- `PUSH_DEPT_MISMATCH` (403) — push в чужой namespace.

### Ссылочная целостность

- `DEPARTMENT_NOT_FOUND` (404), `DEPARTMENT_ALREADY_EXISTS` (409).
- `SERVICE_NOT_FOUND` (404), `SERVICE_ALREADY_EXISTS` (409), `SERVICE_ALREADY_GRANTED` (409).

### Rate-limit / lockout

- `ACCOUNT_TEMPORARILY_LOCKED` (429) — lockout-pipeline. Применяется к `/login`, `/users/me/password` (через `INVALID_OLD_PASSWORD`), `/docker/token`, OAuth `client_credentials` через `/oauth2/token` (per `OAuthClient.failed_secret_attempts` + `OAUTH_CLIENT_LOCKOUT_MINUTES`) и bot-токенам (per `BotAccount.failed_token_attempts` + `BOT_LOCKOUT_MINUTES`). В details — `retry_after_seconds`.

Пример lockout ответа:

```json
{
  "error": "too_many_requests",
  "error_code": "ACCOUNT_TEMPORARILY_LOCKED",
  "message": "Account is temporarily locked due to repeated failed attempts",
  "details": { "retry_after_seconds": 900 },
  "request_id": "req_123"
}
```

### Инфраструктура

5xx ошибки FastAPI обрабатывает дефолтным handler'ом — `auth_service` собственного каталога для них не ведёт. SIEM ловит их через `http.server_error` audit-event с реальным статусом в `details.status_code`.
