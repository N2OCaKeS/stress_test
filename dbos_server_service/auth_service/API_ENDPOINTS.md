# auth_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/auth/v1`.
> **Всего endpoints:** **71** (auth 7, users 12, departments 4, services 3, service_roles 6, tokens 3, bots 9, groups 13, authorization 2, oauth2 5, docker 7).
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
  "error_code": "SERVICE_ACCESS_DENIED",
  "message": "User has no access to config_service",
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

Общий pipeline для `POST /login` и `GET /docker/token` (через `services/_lockout.py`).

| Параметр | ENV | Default |
|---|---|---|
| Порог неудач подряд | `MAX_FAILED_LOGIN_ATTEMPTS` | `5` |
| Длительность блокировки | `LOCKOUT_MINUTES` | `15` |

Поведение:

- Каждая неудачная проверка пароля инкрементит `users.failed_login_attempts` (атомарно).
- При достижении порога ставится `locked_until = now + LOCKOUT_MINUTES`.
- Все последующие попытки (для этого username, любого пароля) → `429 ACCOUNT_TEMPORARILY_LOCKED` с полем `details.retry_after_seconds`.
- Успешный login сбрасывает счётчик и снимает блокировку.
- По истечении `locked_until` следующая попытка автоматически разлочивает аккаунт (CAS-release).
- Lockout общий между `/login` и `/docker/token` — заблокированный аккаунт не пройдёт ни через один из них.

Lockout **не применяется** к refresh, PAT и bot-токенам (там нет brute-force поверхности — токен либо валиден, либо нет).

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

Errors: `USERNAME_TAKEN` (409), `DEPARTMENT_NOT_FOUND` (404), `PERMISSION_DENIED` (403), `DEPARTMENT_ACCESS_DENIED` (403) — department_admin создаёт в чужом отделе, `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — не-account_admin пытается выдать `platform_role`.

### `PATCH /users/{user_id}`

Auth: AnyAdmin. Body (все поля опциональны): `email`, `department_id`, `status` (`UserStatus` enum), `platform_role` (`PlatformRole` enum).

Errors: `USER_NOT_FOUND` (404), `PERMISSION_DENIED` (403).

### `POST /users/{user_id}/roles`

Auth: AnyAdmin. Replace-семантика для (user, service).

```json
{ "service_name": "config_service", "roles": ["reader", "operator"] }
```

Errors: `SERVICE_ACCESS_DENIED` (403), `ROLE_NOT_FOUND` (404).

### `POST /users/{user_id}/reset-password`

Auth: AnyAdmin. `account_admin` — любой юзер; `department_admin` — только свой отдел. Body: `{ "new_password": "<min 8>" }`.

Admin-вариант — для смены чужого пароля. Для self-reset используется `POST /users/me/password` (требует подтверждения старого пароля).

Errors: `USER_NOT_FOUND` (404), `PERMISSION_DENIED` (403), `DEPARTMENT_ISOLATION` (403).

### `POST /users/me/password`

Auth: Bearer (user-context, m2m отбивается). Body:

```json
{ "old_password": "...", "new_password": "<min 8, буквы + цифры>" }
```

Self-reset пароля с обязательным подтверждением `old_password`. После успеха — revoke всех активных сессий юзера (включая текущую), PAT остаются валидными. Audit `user.self_password_reset` (CRITICAL) с `caller_is_admin` в details для SIEM-фильтра по admin-self-reset'ам.

Errors: `INVALID_OLD_PASSWORD` (401, инкрементит lockout-счётчик), `SAME_PASSWORD` (422), `ACCOUNT_TEMPORARILY_LOCKED` (429 + `retry_after_seconds`), `USER_NOT_FOUND` (404).

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

Errors: `GROUP_NOT_FOUND` / `USER_NOT_FOUND` (404), `GROUP_DEPARTMENT_MISMATCH` (400).

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

Errors: `DEPARTMENT_NAME_TAKEN` (409).

### `POST /departments/{department_id}/services`

Auth: `account_admin`. Body: `{ "service_name": "config_service" }`. Создаёт `DepartmentServiceAccess`.

### `DELETE /departments/{department_id}/services/{service_name}`

Auth: `account_admin`. После revoke роли пользователей формально остаются, но эффективно отбрасываются `_merge_permissions` INTERSECT'ом.

---

## Platform services (`/services`)

### `GET /services` / `POST /services` / `DELETE /services/{service_name}`

Auth: `account_admin`.

`POST` body: `{ "service_name": "...", "display_name": "...", "description": "..." }`. После создания автоматически появляется системная роль `admin` (`is_system=True`).

Errors: `SERVICE_NAME_TAKEN` (409), `SERVICE_HAS_DEPENDENCIES` (409) на delete если есть активные `DepartmentServiceAccess`.

---

## Service roles (`/departments/{department_id}/services/{service_name}/roles`)

Управление ролями в scope `(department, service)`. Уникальность по тройке.

### `GET /` / `POST /` / `PATCH /{role_name}` / `DELETE /{role_name}`

Auth: Bearer. `account_admin` / `department_admin` своего отдела / юзер с access к сервису.

`POST` body: `{ "role_name": "...", "display_name": "...", "description": "..." }`.

`PATCH` body: `{ "display_name": "...", "description": "..." }` (опциональны).

Errors:
- `ROLE_NAME_TAKEN` (409) — уже есть в scope.
- `SERVICE_ACCESS_DENIED` (403) — нет dept-service-access.
- `SYSTEM_ROLE_PROTECTED` (400) на DELETE системной роли.

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

Errors: `BOT_NAME_TAKEN` (409), `DEPARTMENT_NOT_FOUND` (404).

### `GET /bots`

Auth: AnyAdmin. `account_admin` — все; `department_admin` — только свой отдел. Пагинация (`limit`/`offset`, см. общие правила) + `X-Total-Count`.

### `PATCH /bots/{bot_id}`

Auth: AnyAdmin. Body (опциональны): `name`, `description`, `status` ("active"|"disabled"), `allowed_services`.

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

Errors: `SERVICE_ACCESS_DENIED` (403) — сервис не в `allowed_services` бота или отдела; `ROLE_NOT_FOUND` (404).

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

Errors: `GROUP_NAME_TAKEN` (409) — имя занято в отделе; `PERMISSION_DENIED` (403) — cross-dept у department_admin.

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

Auth: Bearer (admin своего отдела). Body: `{ "service_name": "config_service" }`. Сервис должен быть в `allowed_services` отдела (иначе `SERVICE_ACCESS_DENIED`).

### `DELETE /groups/{group_id}/services/{service_name}`

Auth: Bearer. После revoke group_service_roles отбрасываются на INTERSECT.

### `GET /groups/{group_id}/roles`

Auth: Bearer. account_admin / department_admin своего отдела / member группы.

### `POST /groups/{group_id}/roles`

Auth: Bearer (admin своего отдела). Replace-семантика. Body:

```json
{ "service_name": "config_service", "roles": ["operator"] }
```

Errors: `SERVICE_ACCESS_DENIED` (403) — нет group_service_access; `ROLE_NOT_FOUND` (404).

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

Errors: `OAUTH_CLIENT_NAME_EXISTS` (409), `DEPARTMENT_ACCESS_DENIED` (403), `redirect_uri_*` (422).

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

Errors: `UNSUPPORTED_RESPONSE_TYPE` (400), `INVALID_REDIRECT_URI` (400), `INVALID_CLIENT` (400), `PKCE_METHOD_INVALID` (400), `USER_CONTEXT_REQUIRED` (403) — m2m-токен на user-endpoint.

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

Errors: `INVALID_GRANT` (400) — code не найден/истёк/уже использован/PKCE verifier не совпал; `INVALID_CLIENT` (401); `UNSUPPORTED_GRANT_TYPE` (400/422); `PKCE_METHOD_INVALID` (400) — `code_challenge_method` не `S256`/`plain`.

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

### `PATCH /docker/registry/{department_id}`

Auth: AnyAdmin. Частичный update. Body — те же поля + `is_enabled` (bool).

### `GET /docker/registry/{department_id}`

Auth: AnyAdmin. Response: `DockerRegistryConfigResponse`. `404` если конфиг не создан.

### `DELETE /docker/registry/{department_id}`

Auth: AnyAdmin. Снимает конфиг полностью.

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

### Валидация / формат запроса

- `INVALID_REQUEST`, `INVALID_JSON`, `MISSING_REQUIRED_FIELD`, `INVALID_FIELD_FORMAT`
- `INVALID_USERNAME_FORMAT`, `INVALID_PASSWORD_FORMAT`, `INVALID_TOKEN_FORMAT`, `INVALID_DATETIME_FORMAT`
- `UNSUPPORTED_AUTH_MODE`, `UNSUPPORTED_GRANT_TYPE`, `UNSUPPORTED_RESPONSE_TYPE`
- `VALIDATION_ERROR`

### Логин и сессии

- `INVALID_CREDENTIALS`, `USER_NOT_FOUND`, `USER_BLOCKED`, `USER_BANNED`
- `PASSWORD_EXPIRED`, `PASSWORD_RESET_REQUIRED`, `LOGIN_ATTEMPTS_EXCEEDED`
- `SESSION_NOT_FOUND`, `SESSION_EXPIRED`, `SESSION_REVOKED`
- `REFRESH_TOKEN_INVALID`, `REFRESH_TOKEN_EXPIRED`, `REFRESH_TOKEN_REVOKED`, `REFRESH_TOKEN_RACE`
- `ACCESS_TOKEN_EXPIRED`, `ACCESS_TOKEN_REVOKED`

### Доступ и авторизация

- `SERVICE_ACCESS_DENIED`, `ROLE_REQUIRED`, `ROLE_NOT_ASSIGNED`, `INSUFFICIENT_SERVICE_ROLE`
- `ACTION_NOT_ALLOWED`, `DEPARTMENT_ACCESS_DENIED`, `SUBJECT_BANNED`
- `BOT_ACCESS_DENIED`, `TOKEN_SCOPE_DENIED`, `TOKEN_SERVICE_NOT_ALLOWED`
- `PERMISSION_DENIED`
- `USER_CONTEXT_REQUIRED` (403) — m2m identity (`actor_type=oauth_client`) на user-facing endpoint'е.

### Service-to-service auth

- `INVALID_SERVICE_TOKEN` (401) — `SERVICE_API_KEY` отсутствует / неверный / per-service mismatch.
- `MISSING_SERVICE_IDENTITY` (401) — header `X-Service-Identity` обязателен, но отсутствует (strict mode либо `SERVICE_API_KEYS` JSON задан).
- `INVALID_SERVICE_IDENTITY` (401) — identity не в `KNOWN_SERVICE_IDENTITIES` (strict mode).
- `SERVICE_IDENTITY_PATH_MISMATCH` (403) — cross-link на loging_service: `X-Service-Identity` не совпадает с path-параметром `/services/{service}/events`.

### Cross-service ссылки

- `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (403) — поднимает `server_service` middleware'ом для `account_admin`/`loging_admin` ДО endpoint-логики. В `auth_service` не эмитится, но клиенты должны его знать (request к server_service через user-JWT).

### PAT / service token

- `TOKEN_NOT_FOUND`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `TOKEN_ALREADY_REVOKED`
- `TOKEN_LIMIT_REACHED`, `TOKEN_NAME_ALREADY_EXISTS`, `TOKEN_CREATION_FORBIDDEN`
- `TOKEN_OWNER_MISMATCH`, `TOKEN_HASH_MISMATCH`, `TOKEN_TYPE_MISMATCH`, `TOKEN_ROTATION_REQUIRED`

### Bot / service account

- `BOT_NOT_FOUND`, `BOT_NAME_TAKEN`, `BOT_BLOCKED`, `BOT_BANNED`
- `BOT_TOKEN_NOT_FOUND`, `BOT_TOKEN_ALREADY_REVOKED`
- `BOT_SERVICE_NOT_ALLOWED`, `BOT_DEPARTMENT_MISMATCH`
- `BOT_CREATION_FORBIDDEN`, `BOT_UPDATE_FORBIDDEN`

### Users / admin

- `USERNAME_TAKEN`, `USER_UPDATE_FORBIDDEN`, `USER_DELETE_FORBIDDEN`, `USER_ROLE_UPDATE_FORBIDDEN`
- `SERVICE_ROLE_ALREADY_ASSIGNED`, `SERVICE_ROLE_NOT_FOUND`, `SERVICE_ROLE_CONFLICT`
- `DEPARTMENT_MISMATCH`, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`
- `CANNOT_ASSIGN_SERVICE`, `CANNOT_ASSIGN_ROLE`
- `PLATFORM_ROLE_ASSIGNMENT_DENIED` (403) — назначить `platform_role` пытается не `account_admin` (на `POST /users`).
- `BAN_ALREADY_ACTIVE`, `BAN_NOT_FOUND`, `UNBAN_FORBIDDEN`

### Groups

- `GROUP_NAME_TAKEN`, `GROUP_NOT_FOUND`, `GROUP_DEPARTMENT_MISMATCH`

### Service roles

- `ROLE_NAME_TAKEN`, `ROLE_NOT_FOUND`, `SYSTEM_ROLE_PROTECTED`

### OAuth2

- `INVALID_GRANT` (400) — code не найден/истёк/уже использован/redirect_uri mismatch/PKCE verifier mismatch.
- `INVALID_CLIENT` (401) — неверный client_id или secret.
- `OAUTH_CLIENT_INVALID`, `OAUTH_CLIENT_NAME_EXISTS` (409).
- `OAUTH_CODE_INVALID`, `REDIRECT_URI_MISMATCH`, `INVALID_REDIRECT_URI` (400/422).
- `GRANT_TYPE_NOT_ALLOWED`, `UNSUPPORTED_GRANT_TYPE` (400/422), `UNSUPPORTED_RESPONSE_TYPE` (400).
- `PKCE_METHOD_INVALID` (400) — `code_challenge_method` отличается от `S256`/`plain`.
- `redirect_uri_invalid`, `redirect_uri_has_fragment`, `redirect_uri_not_https`, `redirect_uri_scheme_invalid` (Pydantic 422) — валидация `redirect_uris` на регистрации клиента.

### Docker

- `DOCKER_ACCESS_DENIED`, `MISSING_CREDENTIALS`

### Ссылочная целостность

- `DEPARTMENT_NOT_FOUND`, `SERVICE_NOT_FOUND`, `RESOURCE_NOT_FOUND`, `DEPENDENCY_NOT_FOUND`
- `SERVICE_HAS_DEPENDENCIES`, `SERVICE_NAME_TAKEN`, `DEPARTMENT_NAME_TAKEN`

### Rate-limit / lockout

- `TOO_MANY_REQUESTS`, `TOO_MANY_LOGIN_ATTEMPTS`, `TOO_MANY_TOKEN_ATTEMPTS`
- `SUSPICIOUS_ACTIVITY_DETECTED`, `ACCOUNT_TEMPORARILY_LOCKED`, `IP_TEMPORARILY_BLOCKED`

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

- `INTERNAL_ERROR`, `DATABASE_ERROR`, `HASHING_ERROR`
- `TOKEN_SIGNING_ERROR`, `TOKEN_VERIFICATION_ERROR`
- `DEPENDENCY_UNAVAILABLE`, `LOGGING_SERVICE_UNAVAILABLE`, `CONFIGURATION_ERROR`
