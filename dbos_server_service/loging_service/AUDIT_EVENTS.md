# Справочник событий аудита

Все события отправляются из `auth_service` в `logging_service`.  
Уровень важности (`severity`) вычисляется автоматически в `audit_service.py`  
по таблице ниже — если не указан явно при вызове `emit()`.

## Уровни важности (от минимального к максимальному)

| Уровень | Типичное применение |
|---------|---------------------|
| `TRACE` | Зарезервирован для отладки, не используется в коде |
| `DEBUG` | Зарезервирован для диагностики, не используется в коде |
| `INFO` | Успешные операции чтения, список, получение данных |
| `WARNING` | Операции создания/управления ботами, токенами; мягкие нарушения доступа |
| `ERROR` | Доступен для переопределения через правила; в коде не назначается по умолчанию |
| `CRITICAL` | Критические операции безопасности: сброс пароля, бан, выдача OAuth-клиентов; ошибки аутентификации |

Логика по умолчанию (код → `_DEFAULT_SEVERITY` в `audit_service.py`):

- Каждая пара `(action, status)` имеет явное значение в таблице ниже
- Для неизвестных действий: `status=failure|denied` → `WARNING`, иначе → `INFO`

Правила аудита (`/api/logging/v1/rules`) позволяют переопределить severity
или подавить любую группу событий без изменения кода.

---

## Соглашение об именовании действий

Все действия используют формат `<object>.<verb>`, например:
- `user.create`, `user.ban`, `user.password_reset`
- `pat.create`, `pat.revoke`
- `bot.token_create`, `bot.token_revoke`
- `group.service_grant`, `group.roles_assign`

Исключения — составные объекты в одном сегменте: `service_role`, `oauth_client`, `docker_registry`.

---

## HTTP-уровень (middleware, все эндпоинты кроме /health и /ready)

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `http.access_denied` | `denied` | CRITICAL | HTTP 401 или 403 |
| `http.client_error` | `failure` | WARNING | HTTP 4xx (кроме 401/403) |
| `http.server_error` | `failure` | CRITICAL | HTTP 5xx |

---

## Аутентификация и сессии

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `user.login` | `success` | INFO | Успешный вход |
| `user.login` | `failure` | CRITICAL | Неверный пароль, пользователь не найден, аккаунт заблокирован, забанен |
| `user.refresh` | `success` | INFO | Успешное обновление access-токена |
| `user.logout` | `success` | INFO | Выход из системы |
| `user.me` | `success` | INFO | Запрос информации о текущем пользователе |
| `token.refresh_reuse` | `failure` | CRITICAL | Повторное использование уже использованного refresh-токена |

---

## Пользователи

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `user.create` | `success` | INFO | Создание пользователя |
| `user.update` | `success` | INFO | Обновление данных пользователя |
| `user.roles_assign` | `success` | INFO | Назначение сервисных ролей пользователю |
| `user.password_reset` | `success` | CRITICAL | Сброс пароля пользователя |
| `user.ban` | `success` | CRITICAL | Блокировка пользователя |
| `user.unban` | `success` | CRITICAL | Разблокировка пользователя |

---

## Отделы

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `department.create` | `success` | CRITICAL | Создание отдела |
| `department.list` | `success` | INFO | Просмотр списка отделов |
| `department.service_grant` | `success` | CRITICAL | Выдача отделу доступа к сервису |
| `department.service_revoke` | `success` | CRITICAL | Отзыв доступа отдела к сервису |

---

## Группы

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `group.create` | `success` | INFO | Создание группы |
| `group.update` | `success` | INFO | Обновление группы |
| `group.delete` | `success` | CRITICAL | Удаление группы |
| `group.member_add` | `success` | WARNING | Добавление пользователя в группу |
| `group.member_remove` | `success` | WARNING | Удаление пользователя из группы |
| `group.service_grant` | `success` | CRITICAL | Выдача группе доступа к сервису |
| `group.service_revoke` | `success` | CRITICAL | Отзыв доступа группы к сервису |
| `group.roles_assign` | `success` | CRITICAL | Назначение ролей группе |
| `group.roles_revoke` | `success` | CRITICAL | Отзыв ролей у группы |

---

## Платформенные сервисы

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `service.create` | `success` | CRITICAL | Регистрация нового сервиса на платформе |
| `service.delete` | `success` | CRITICAL | Удаление сервиса с платформы |
| `service.list` | `success` | INFO | Просмотр списка сервисов |

---

## Роли сервисов

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `service_role.create` | `success` | INFO | Создание определения роли для сервиса |
| `service_role.update` | `success` | INFO | Обновление определения роли |
| `service_role.delete` | `success` | CRITICAL | Удаление определения роли |
| `service_role.bulk_assign` | `success` | INFO | Массовое назначение роли пользователям |
| `service_role.bulk_revoke` | `success` | INFO | Массовый отзыв роли у пользователей |

---

## Личные токены доступа (PAT)

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `pat.create` | `success` | INFO | Создание Personal Access Token |
| `pat.list` | `success` | INFO | Просмотр списка PAT |
| `pat.revoke` | `success` | WARNING | Отзыв Personal Access Token |

---

## Боты

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `bot.create` | `success` | WARNING | Создание сервисного бота |
| `bot.list` | `success` | INFO | Просмотр списка ботов |
| `bot.update` | `success` | WARNING | Обновление данных бота |
| `bot.token_create` | `success` | WARNING | Создание токена для бота |
| `bot.token_list` | `success` | INFO | Просмотр списка токенов бота |
| `bot.token_revoke` | `success` | WARNING | Отзыв токена бота |

---

## OAuth2-клиенты

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `oauth_client.create` | `success` | CRITICAL | Регистрация OAuth2-клиента |
| `oauth_client.list` | `success` | INFO | Просмотр списка OAuth2-клиентов |
| `oauth_client.delete` | `success` | CRITICAL | Удаление OAuth2-клиента |
| `oauth.authorization_code_issued` | `success` | INFO | Выдача authorization code |
| `oauth.code_exchanged` | `success` | INFO | Обмен code на токен |
| `oauth.client_credentials_token` | `success` | INFO | Выдача токена по client_credentials |

---

## Интроспекция токенов

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `token.introspect` | `success` | INFO | Успешная проверка токена (access, PAT, bot) |
| `token.introspect` | `failure` | CRITICAL | Токен истёк, неактивен, или бот отключён |

---

## Проверка доступа к сервису

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `service.access_check` | `success` | INFO | Успешная проверка доступа к сервису |
| `service.access_check` | `denied` | WARNING | Доступ к сервису запрещён |

---

## Docker Registry

| action | status | severity | Когда возникает |
|--------|--------|----------|-----------------|
| `docker_registry.configure` | `success` | CRITICAL | Включение/настройка registry для отдела |
| `docker_registry.update` | `success` | CRITICAL | Обновление настроек registry |
| `docker_registry.get_config` | `success` | INFO | Просмотр настроек registry |
| `docker_registry.disable` | `success` | CRITICAL | Отключение registry для отдела |
| `docker.token_issued` | `success` | INFO | Выдача JWT-токена для Docker registry |

---

## Изменение уровней важности через правила

Уровни в таблице выше — значения **по умолчанию** из кода.  
Чтобы изменить без деплоя, создайте правило через API:

```http
POST /api/logging/v1/rules
Authorization: Bearer <SERVICE_API_KEY>

{
  "name": "escalate-pat-creation",
  "match_action": "pat.create",
  "effect": "OVERRIDE_SEVERITY",
  "effect_severity": "CRITICAL",
  "priority": 200
}
```

Поддерживаемые эффекты:
- `OVERRIDE_SEVERITY` + `effect_severity` — изменить уровень важности
- `SUPPRESS` — не сохранять событие вообще
- `ALLOW` — сохранить немедленно (используется для исключений из SUPPRESS-правил)

Доступные значения `effect_severity`: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

Критерии совпадения: `match_service`, `match_action` (поддерживает glob `user.*`),
`match_status`, `match_severity`, `match_allowed`.
