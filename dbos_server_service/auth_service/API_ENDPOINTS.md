# auth_service API endpoints

## Назначение

Этот документ фиксирует рекомендуемые endpoint'ы для `auth_service` с учётом текущей модели:

- `web_settings` использует web-сессию;
- `cli` использует либо логин/пароль, либо персональный токен;
- автоматизации и боты используют отдельные service/bot tokens напрямую через API;
- роли пользователей назначаются только в `auth_service`;
- доступ отдела к конкретному сервису выдаёт только `account_admin`;
- только `account_admin` может создавать и удалять сервисы платформы;
- прикладные сервисы проверяют базовую роль, отдел, доступные сервисы и затем применяют свою матрицу прав.

## Базовые принципы

- все endpoint'ы работают только по `HTTPS`;
- все версии API начинаются с `/api/auth/v1`;
- access token короткоживущий;
- refresh token ротируется;
- PAT и bot token показываются только один раз при создании;
- в БД хранится только хэш токена;
- все значимые auth-события отправляются в `logging_service`.

Дополнительное правило доступа:

- только `account_admin` может выдать отделу доступ к сервису;
- только `account_admin` может добавить новый сервис в список сервисов, доступных отделу;
- только `account_admin` может создать новый сервис как сущность платформы;
- только `account_admin` может удалить сервис из платформы;
- если у отдела нет доступа к сервису, пользователи этого отдела не могут получать роли этого сервиса;
- если у отдела нет доступа к сервису, нельзя создать администратора этого сервиса для данного отдела;
- если у отдела нет доступа к сервису, любые запросы к этому сервису должны завершаться отказом в доступе.

## Формат identity context

Во многих ответах `auth_service` полезно возвращать одинаковый контекст пользователя:

```json
{
  "user_id": "usr_123",
  "username": "ivanov",
  "department_id": "dep_nt",
  "department_name": "НТ",
  "allowed_services": ["config_service", "server_service"],
  "service_roles": {
    "config_service": ["reader", "operator"],
    "server_service": ["admin"]
  },
  "is_banned": false
}
```

Важно:

- в одном сервисе у пользователя может быть несколько ролей одновременно;
- итоговые права внутри сервиса определяются объединением прав всех назначенных ролей;
- `auth_service` хранит и возвращает именно список ролей по каждому сервису.
- набор ролей по сервису может существовать только если сам сервис предварительно разрешён для отдела.

## Web session endpoints

### `POST /api/auth/v1/login`

Назначение:
логин пользователя по `username/password` для web и CLI.

Request:

```json
{
  "username": "ivanov",
  "password": "secret"
}
```

Поведение:

- для `web_settings` endpoint создаёт серверную сессию и выставляет cookie;
- для `cli` endpoint может дополнительно вернуть access/refresh tokens;
- при неверном пароле увеличивает счётчик неудачных попыток;
- после серии неудачных попыток может заблокировать учётную запись.

Пример успешного ответа для CLI:

```json
{
  "access_token": "jwt-access-token",
  "refresh_token": "opaque-refresh-token",
  "token_type": "Bearer",
  "expires_in": 600,
  "identity": {
    "user_id": "usr_123",
    "username": "ivanov",
    "department_id": "dep_nt",
    "allowed_services": ["config_service"],
    "service_roles": {
      "config_service": ["reader", "operator"]
    }
  }
}
```

Ошибки:

- `400` invalid request
- `401` invalid credentials
- `403` banned user
- `429` too many attempts

### `POST /api/auth/v1/refresh`

Назначение:
обновление access token по refresh token.

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Успешный ответ:

```json
{
  "access_token": "new-access-token",
  "refresh_token": "new-refresh-token",
  "token_type": "Bearer",
  "expires_in": 600
}
```

Поведение:

- refresh token всегда ротируется;
- старый refresh token после успешного обмена помечается использованным;
- повторное использование старого refresh token должно считаться подозрительным событием.

Ошибки:

- `401` invalid refresh token
- `403` banned user

### `POST /api/auth/v1/logout`

Назначение:
завершение web-сессии или CLI-сессии.

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Поведение:

- инвалидирует refresh token;
- закрывает связанную сессию;
- для web удаляет auth cookie.

Успешный ответ:

```json
{
  "ok": true
}
```

### `GET /api/auth/v1/me`

Назначение:
получение текущего identity context.

Auth:

- bearer token
- или web-session cookie

Успешный ответ:

```json
{
  "user_id": "usr_123",
  "username": "ivanov",
  "department_id": "dep_nt",
  "department_name": "НТ",
  "allowed_services": ["config_service", "server_service"],
  "service_roles": {
    "config_service": ["reader", "operator"],
    "server_service": ["admin"]
  },
  "is_banned": false
}
```

## Personal access token endpoints

### `POST /api/auth/v1/tokens`

Назначение:
создание персонального токена пользователя.

Auth:

- только аутентифицированный пользователь

Request:

```json
{
  "name": "cli-token",
  "expires_at": "2026-05-01T00:00:00Z",
  "allowed_services": ["config_service", "server_service"]
}
```

Успешный ответ:

```json
{
  "token_id": "pat_123",
  "token": "dbos_pat_xxxxxxxxx",
  "name": "cli-token",
  "expires_at": "2026-05-01T00:00:00Z"
}
```

Важно:

- реальное значение токена возвращается только один раз;
- в БД хранится только хэш токена;
- токен не может расширять права пользователя сверх уже выданных ему через `auth_service`.

### `GET /api/auth/v1/tokens`

Назначение:
получение списка собственных PAT без секретных значений.

Успешный ответ:

```json
[
  {
    "token_id": "pat_123",
    "name": "cli-token",
    "created_at": "2026-04-18T10:00:00Z",
    "expires_at": "2026-05-01T00:00:00Z",
    "last_used_at": "2026-04-18T12:00:00Z",
    "revoked_at": null
  }
]
```

### `DELETE /api/auth/v1/tokens/{token_id}`

Назначение:
отзыв собственного PAT.

Успешный ответ:

```json
{
  "ok": true
}
```

## Bot and service token endpoints

### `POST /api/auth/v1/bots`

Назначение:
создание bot/service account.

Auth:

- `account_admin`
- или `department_admin` только в рамках своего отдела

Request:

```json
{
  "name": "git_bot",
  "department_id": "dep_nt",
  "allowed_services": ["config_service"],
  "description": "bot for git automation"
}
```

Успешный ответ:

```json
{
  "bot_id": "bot_123",
  "name": "git_bot",
  "department_id": "dep_nt",
  "allowed_services": ["config_service"],
  "is_active": true
}
```

### `GET /api/auth/v1/bots`

Назначение:
получение списка bot/service accounts в разрешённой зоне видимости.

### `PATCH /api/auth/v1/bots/{bot_id}`

Назначение:
изменение bot/service account.

Можно менять:

- название;
- описание;
- статус `active/blocked`;
- список разрешённых сервисов.

### `POST /api/auth/v1/bots/{bot_id}/tokens`

Назначение:
выпуск service token для bot account.

Request:

```json
{
  "name": "git-bot-main-token",
  "expires_at": "2026-06-01T00:00:00Z"
}
```

Успешный ответ:

```json
{
  "token_id": "bot_tok_123",
  "token": "dbos_bot_xxxxxxxxx",
  "expires_at": "2026-06-01T00:00:00Z"
}
```

Важно:

- токен возвращается только один раз;
- хранится только хэш;
- все вызовы таким токеном идут от имени bot account, а не обычного пользователя.

### `GET /api/auth/v1/bots/{bot_id}/tokens`

Назначение:
список токенов bot account без секретных значений.

### `DELETE /api/auth/v1/bots/{bot_id}/tokens/{token_id}`

Назначение:
отзыв service token.

## Authorization and validation endpoints

### `POST /api/auth/v1/introspect`

Назначение:
проверка токена backend-сервисом.

Request:

```json
{
  "token": "bearer-token"
}
```

Успешный ответ:

```json
{
  "active": true,
  "subject_type": "user",
  "sub": "usr_123",
  "department_id": "dep_nt",
  "allowed_services": ["config_service"],
  "service_roles": {
    "config_service": ["reader", "operator"]
  },
  "exp": 1770000000
}
```

Примечание:

- если используете self-contained JWT, backend может валидировать токен локально;
- introspection полезен для opaque token или быстрого revoke-check.

### `POST /api/auth/v1/authorize/service-access`

Назначение:
проверка, доступен ли сервис пользователю или боту.

Request:

```json
{
  "subject_token": "bearer-token",
  "service_name": "config_service"
}
```

Успешный ответ:

```json
{
  "allowed": true,
  "department_id": "dep_nt",
  "service_roles": ["reader", "operator"]
}
```

Этот endpoint полезен как явный контракт между `auth_service` и прикладными сервисами.

Если сервис не разрешён отделу, ответ должен быть таким:

```json
{
  "allowed": false,
  "department_id": "dep_nt",
  "service_roles": []
}
```

## Admin endpoints

### `POST /api/auth/v1/services`

Назначение:
создание нового сервиса в платформе.

Auth:

- только `account_admin`

Request:

```json
{
  "service_name": "config_service",
  "display_name": "Config Service",
  "description": "service for secrets and integration tokens"
}
```

Успешный ответ:

```json
{
  "service_name": "config_service",
  "display_name": "Config Service",
  "is_active": true
}
```

Важно:

- после создания сервис ещё не обязан быть доступен всем отделам;
- доступ отделам настраивается отдельно через `departments/{department_id}/services`;
- создание сервиса должно логироваться как критичное административное действие.

### `DELETE /api/auth/v1/services/{service_name}`

Назначение:
удаление сервиса из платформы.

Auth:

- только `account_admin`

Поведение:

- удаляет или архивирует сервис как сущность платформы по выбранной политике;
- удаляет или отключает связи `department -> service`;
- роли пользователей по этому сервису должны быть очищены или помечены неактивными;
- все PAT, bot tokens и пользовательские права, связанные только с этим сервисом, должны быть пересчитаны по политике безопасности;
- обязательно логируется как критичное административное действие.

### `POST /api/auth/v1/departments/{department_id}/services`

Назначение:
выдать отделу доступ к сервису.

Auth:

- только `account_admin`

Request:

```json
{
  "service_name": "config_service"
}
```

Успешный ответ:

```json
{
  "department_id": "dep_nt",
  "service_name": "config_service",
  "enabled": true
}
```

Важно:

- только после этого пользователям отдела можно назначать роли для данного сервиса;
- только после этого внутри отдела можно создавать администраторов данного сервиса;
- без этого шага доступ к сервису должен считаться полностью запрещённым.

### `DELETE /api/auth/v1/departments/{department_id}/services/{service_name}`

Назначение:
отозвать у отдела доступ к сервису.

Auth:

- только `account_admin`

Поведение:

- запрещает дальнейшие обращения отдела к сервису;
- инвалидирует прикладной доступ к сервису для всех пользователей отдела;
- роли этого сервиса у пользователей отдела должны считаться неактивными или быть удалены по выбранной политике;
- обязательно логируется как критичное административное действие.

### `POST /api/auth/v1/users`

Назначение:
создание пользователя.

Auth:

- `account_admin` для любого отдела;
- `department_admin` только для своего отдела.

### `PATCH /api/auth/v1/users/{user_id}`

Назначение:
изменение пользователя.

Можно менять:

- отдел;
- список доступных сервисов;
- список ролей по каждому сервису;
- статус `active/blocked`.

Важно:

- назначать роли сервиса пользователю можно только если этот сервис уже разрешён его отделу;
- если сервис не разрешён отделу, запрос должен завершаться ошибкой `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`.

### `POST /api/auth/v1/users/{user_id}/reset-password`

Назначение:
сброс пароля пользователя.

Поведение:

- инвалидирует все refresh token;
- может инвалидировать все PAT по политике безопасности;
- обязательно логируется.

### `POST /api/auth/v1/users/{user_id}/ban`

Назначение:
бан пользователя.

Поведение:

- блокирует новые входы;
- инвалидирует активные сессии;
- инвалидирует refresh token;
- при необходимости отзывает PAT.

### `POST /api/auth/v1/users/{user_id}/unban`

Назначение:
снятие бана.

## Рекомендуемые ошибки

### Общий формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "SERVICE_ACCESS_DENIED",
  "message": "User has no access to config_service",
  "details": {
    "service_name": "config_service"
  },
  "request_id": "req_123",
  "timestamp": "2026-04-18T12:00:00Z"
}
```

Где:

- `error` — короткая техническая категория;
- `error_code` — стабильный доменный код ошибки;
- `message` — читаемое описание;
- `details` — структурированные детали для клиента;
- `request_id` — идентификатор запроса для трассировки;
- `timestamp` — время ошибки.

### Базовые HTTP-коды

- `400` bad request
- `401` unauthorized
- `403` forbidden
- `404` not found
- `409` conflict
- `422` validation error
- `429` too many requests
- `500` internal error
- `503` service unavailable

### Ошибки валидации и формата запроса

- `INVALID_REQUEST`
- `INVALID_JSON`
- `MISSING_REQUIRED_FIELD`
- `INVALID_FIELD_FORMAT`
- `INVALID_USERNAME_FORMAT`
- `INVALID_PASSWORD_FORMAT`
- `INVALID_TOKEN_FORMAT`
- `INVALID_DATETIME_FORMAT`
- `UNSUPPORTED_AUTH_MODE`
- `UNSUPPORTED_GRANT_TYPE`
- `VALIDATION_ERROR`

Пример:

```json
{
  "error": "validation_error",
  "error_code": "MISSING_REQUIRED_FIELD",
  "message": "Field expires_at is required",
  "details": {
    "field": "expires_at"
  },
  "request_id": "req_123"
}
```

### Ошибки логина и сессии

- `INVALID_CREDENTIALS`
- `USER_NOT_FOUND`
- `USER_BLOCKED`
- `USER_BANNED`
- `PASSWORD_EXPIRED`
- `PASSWORD_RESET_REQUIRED`
- `LOGIN_ATTEMPTS_EXCEEDED`
- `SESSION_NOT_FOUND`
- `SESSION_EXPIRED`
- `SESSION_REVOKED`
- `REFRESH_TOKEN_INVALID`
- `REFRESH_TOKEN_EXPIRED`
- `REFRESH_TOKEN_REVOKED`
- `REFRESH_TOKEN_REUSED`
- `ACCESS_TOKEN_EXPIRED`
- `ACCESS_TOKEN_REVOKED`

Пример:

```json
{
  "error": "unauthorized",
  "error_code": "INVALID_CREDENTIALS",
  "message": "Invalid username or password",
  "request_id": "req_123"
}
```

### Ошибки доступа и авторизации

- `SERVICE_ACCESS_DENIED`
- `ROLE_REQUIRED`
- `ROLE_NOT_ASSIGNED`
- `INSUFFICIENT_SERVICE_ROLE`
- `ACTION_NOT_ALLOWED`
- `DEPARTMENT_ACCESS_DENIED`
- `SUBJECT_BANNED`
- `BOT_ACCESS_DENIED`
- `TOKEN_SCOPE_DENIED`
- `TOKEN_SERVICE_NOT_ALLOWED`

Пример:

```json
{
  "error": "forbidden",
  "error_code": "INSUFFICIENT_SERVICE_ROLE",
  "message": "Assigned roles do not allow this action",
  "details": {
    "service_name": "config_service",
    "required_permission": "token.read",
    "assigned_roles": ["reader"]
  },
  "request_id": "req_123"
}
```

### Ошибки PAT и service token

- `TOKEN_NOT_FOUND`
- `TOKEN_EXPIRED`
- `TOKEN_REVOKED`
- `TOKEN_ALREADY_REVOKED`
- `TOKEN_LIMIT_REACHED`
- `TOKEN_NAME_ALREADY_EXISTS`
- `TOKEN_CREATION_FORBIDDEN`
- `TOKEN_OWNER_MISMATCH`
- `TOKEN_HASH_MISMATCH`
- `TOKEN_TYPE_MISMATCH`
- `TOKEN_ROTATION_REQUIRED`

Пример:

```json
{
  "error": "conflict",
  "error_code": "TOKEN_ALREADY_REVOKED",
  "message": "Token is already revoked",
  "details": {
    "token_id": "pat_123"
  },
  "request_id": "req_123"
}
```

### Ошибки bot/service account

- `BOT_NOT_FOUND`
- `BOT_ALREADY_EXISTS`
- `BOT_BLOCKED`
- `BOT_BANNED`
- `BOT_TOKEN_NOT_FOUND`
- `BOT_TOKEN_ALREADY_REVOKED`
- `BOT_SERVICE_NOT_ALLOWED`
- `BOT_DEPARTMENT_MISMATCH`
- `BOT_CREATION_FORBIDDEN`
- `BOT_UPDATE_FORBIDDEN`

### Ошибки пользователей и администрирования

- `USER_ALREADY_EXISTS`
- `USER_UPDATE_FORBIDDEN`
- `USER_DELETE_FORBIDDEN`
- `USER_ROLE_UPDATE_FORBIDDEN`
- `SERVICE_ROLE_ALREADY_ASSIGNED`
- `SERVICE_ROLE_NOT_FOUND`
- `SERVICE_ROLE_CONFLICT`
- `DEPARTMENT_MISMATCH`
- `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`
- `CANNOT_ASSIGN_SERVICE`
- `CANNOT_ASSIGN_ROLE`
- `BAN_ALREADY_ACTIVE`
- `BAN_NOT_FOUND`
- `UNBAN_FORBIDDEN`

### Ошибки ресурсов и ссылочной целостности

- `DEPARTMENT_NOT_FOUND`
- `SERVICE_NOT_FOUND`
- `ROLE_NOT_FOUND`
- `RESOURCE_NOT_FOUND`
- `DEPENDENCY_NOT_FOUND`

### Ошибки rate limiting и защиты

- `TOO_MANY_REQUESTS`
- `TOO_MANY_LOGIN_ATTEMPTS`
- `TOO_MANY_TOKEN_ATTEMPTS`
- `SUSPICIOUS_ACTIVITY_DETECTED`
- `ACCOUNT_TEMPORARILY_LOCKED`
- `IP_TEMPORARILY_BLOCKED`

Пример:

```json
{
  "error": "too_many_requests",
  "error_code": "ACCOUNT_TEMPORARILY_LOCKED",
  "message": "Account is temporarily locked due to repeated failed attempts",
  "details": {
    "retry_after_seconds": 900
  },
  "request_id": "req_123"
}
```

### Ошибки инфраструктуры

- `INTERNAL_ERROR`
- `DATABASE_ERROR`
- `HASHING_ERROR`
- `TOKEN_SIGNING_ERROR`
- `TOKEN_VERIFICATION_ERROR`
- `DEPENDENCY_UNAVAILABLE`
- `LOGGING_SERVICE_UNAVAILABLE`
- `CONFIGURATION_ERROR`

### Рекомендации по заголовкам ответа

Полезно добавлять:

- `X-Request-ID`
- `Retry-After` для `429`
- `WWW-Authenticate` для `401`, если используется bearer auth

## Что логировать в logging_service

- успешный login;
- неуспешный login;
- refresh token rotation;
- logout;
- создание PAT;
- отзыв PAT;
- создание bot account;
- выпуск bot token;
- отзыв bot token;
- бан и разбан;
- смену пароля;
- попытку доступа к запрещённому сервису;
- introspection/authorization deny для критичных случаев.

## Что можно сделать следующим шагом

- зафиксировать OpenAPI schema;
- описать таблицы БД для пользователей, refresh token, PAT, bot tokens и банов;
- отдельно описать claims для JWT access token;
- определить, какие endpoint'ы используют cookie-session, а какие только bearer token.
