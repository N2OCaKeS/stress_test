# auth_service

Центральный сервис аутентификации и авторизации платформы DBOS Server Manager.

Все остальные сервисы (`server_service`, `config_service`, `logging_service`) доверяют только `auth_service` в вопросах того, кто есть кто и что кому разрешено.

---

## Оглавление

- [Архитектура прав доступа](#архитектура-прав-доступа)
- [Модели данных](#модели-данных)
- [API — обзор эндпоинтов](#api--обзор-эндпоинтов)
- [Токены](#токены)
- [Интроспекция и проверка доступа](#интроспекция-и-проверка-доступа)
- [Боты](#боты)
- [Docker registry](#docker-registry)
- [OAuth2](#oauth2)
- [Запуск и развёртывание](#запуск-и-развёртывание)

---

## Архитектура прав доступа

### Уровни ролей

Система работает с двумя независимыми уровнями ролей:

**Platform roles** — роли внутри `auth_service`, управляют административными возможностями:

| Роль | Что может |
|------|-----------|
| `account_admin` | Создаёт/удаляет сервисы, отделы, пользователей любого отдела; выдаёт отделам доступ к сервисам; управляет группами. Не получает прикладных прав в других сервисах. |
| `department_admin` | Создаёт и управляет пользователями только своего отдела. Может добавлять пользователей своего отдела в группы. |
| *(нет роли)* | Обычный пользователь — только читает собственный профиль и токены. |

**Service roles** — роли внутри конкретного прикладного сервиса. Определяются динамически для каждого сервиса. При создании сервиса автоматически создаётся роль `admin`. Примеры типичных ролей: `guest`, `reader`, `operator`, `admin`. Набор ролей управляется отдельно для каждого сервиса.

### Как формируются эффективные права пользователя

При логине (и при refresh) `auth_service` собирает итоговый набор прав из **трёх источников**:

```
Эффективные allowed_services = dept_services ∪ group_services
Эффективные service_roles    = direct_roles  ∪ group_roles   (union per service)
```

1. **Отдел** — сервисы, доступ к которым явно выдан отделу пользователя.
2. **Группы** — сервисы и роли, унаследованные через членство в группах.
3. **Прямые назначения** — роли, назначенные пользователю персонально.

Права **аддитивны**: группы и прямые назначения только расширяют доступ, но не сужают. Чтобы убрать доступ — нужно отозвать его явно.

`account_admin` всегда получает пустые `allowed_services` и `service_roles` — платформенный администратор не является пользователем прикладных сервисов.

### Identity Context — что встроено в токен

Каждый access token (JWT) содержит:

```json
{
  "sub": "usr_abc123",
  "username": "ivanov",
  "department_id": "dep_xyz",
  "platform_role": null,
  "allowed_services": ["config_service", "server_service"],
  "service_roles": {
    "config_service": ["reader", "operator"],
    "server_service": ["reader"]
  }
}
```

Это же возвращает `/me` и `/authorization/introspect`.

### Инвариант: сервис → отдел → пользователь

Прежде чем назначить роль пользователю на сервис, отдел пользователя **обязан** иметь доступ к этому сервису. Система проверяет это принудительно:

```
account_admin выдаёт сервис отделу
    → department_admin или account_admin назначает роль пользователю
    → при логине права собираются автоматически
```

---

## Модели данных

### Пользователи и отделы

```
Department
  ├── users[]          ← User.department_id
  └── service_access[] ← DepartmentServiceAccess (какие сервисы разрешены отделу)

User
  ├── platform_role    ← account_admin / department_admin / null
  ├── service_roles[]  ← UserServiceRole (прямые назначения)
  └── group_memberships[] ← UserGroupMembership
```

### Сервисы и роли

```
PlatformService
  ├── role_definitions[] ← ServiceRoleDefinition (какие роли существуют)
  │     └── при создании сервиса автоматически создаётся роль "admin"
  └── department_access[] ← DepartmentServiceAccess

ServiceRoleDefinition
  └── при удалении роли — автоматически снимается у всех пользователей и групп
```

### Группы

```
UserGroup
  ├── memberships[]    ← UserGroupMembership (кто входит)
  ├── service_access[] ← GroupServiceAccess (к каким сервисам)
  └── service_roles[]  ← GroupServiceRole (роли на сервисах)
```

Группы — это способ выдать одинаковый набор прав сразу многим пользователям, независимо от их отдела.

### Токены

| Тип | Префикс | Хранится | Назначение |
|-----|---------|----------|------------|
| JWT access | — | только в payload | Краткоживущий (минуты), встроен в запросы |
| Refresh token | — | хэш в `sessions` | Ротация access-токенов |
| PAT | `dbos_pat_` | хэш в `personal_access_tokens` | Долгосрочный токен пользователя |
| Bot token | `dbos_bot_` | хэш в `bot_tokens` | Токен для автоматизации |

Все непрозрачные токены (PAT, bot, refresh) хранятся **только в виде хэша**. Сырое значение возвращается один раз при создании и больше нигде не доступно.

---

## API — обзор эндпоинтов

Все эндпоинты — `GET /api/auth/v1/...`. Swagger: `http://localhost:8000/docs`

### Аутентификация

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/login` | Логин по username/password → access + refresh токен |
| `POST` | `/refresh` | Ротация refresh-токена → новый access + refresh |
| `POST` | `/logout` | Отзыв refresh-токена |
| `GET` | `/me` | Текущий identity context |
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe (проверяет БД) |

### Пользователи (`/users`)

| Метод | URL | Кто может | Описание |
|-------|-----|-----------|----------|
| `POST` | `/users` | AnyAdmin | Создание пользователя; поддерживает `initial_roles` |
| `PATCH` | `/users/{id}` | AnyAdmin | Обновление профиля |
| `POST` | `/users/{id}/roles` | AnyAdmin | Назначение ролей на сервис |
| `GET` | `/users/{id}/groups` | Сам пользователь / AnyAdmin | Группы пользователя |
| `POST` | `/users/{id}/groups` | AnyAdmin | Добавить пользователя в группу |
| `DELETE` | `/users/{id}/groups/{group_id}` | AnyAdmin | Убрать из группы |
| `POST` | `/users/{id}/reset-password` | AnyAdmin | Сброс пароля |
| `POST` | `/users/{id}/ban` | AccountAdmin | Бан пользователя |
| `POST` | `/users/{id}/unban` | AccountAdmin | Разбан |

При создании пользователя можно сразу задать роли:

```json
POST /users
{
  "username": "ivanov",
  "password": "...",
  "department_id": "dep_xyz",
  "initial_roles": [
    { "service_name": "config_service", "roles": ["reader", "operator"] }
  ]
}
```

### Отделы (`/departments`)

| Метод | URL | Кто может | Описание |
|-------|-----|-----------|----------|
| `GET` | `/departments` | AnyAdmin | Список отделов |
| `POST` | `/departments` | AccountAdmin | Создание отдела |
| `POST` | `/departments/{id}/services` | AccountAdmin | Выдать отделу доступ к сервису |
| `DELETE` | `/departments/{id}/services/{svc}` | AccountAdmin | Отозвать доступ |

### Сервисы (`/services`)

| Метод | URL | Кто может | Описание |
|-------|-----|-----------|----------|
| `GET` | `/services` | AccountAdmin | Список сервисов |
| `POST` | `/services` | AccountAdmin | Создать сервис (автоматически создаёт роль `admin`) |
| `DELETE` | `/services/{svc}` | AccountAdmin | Удалить сервис (деактивирует все роли, доступы) |

### Роли сервиса (`/services/{svc}/roles`)

Кто может управлять: `account_admin` **или** пользователь с ролью `admin` данного сервиса.

| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/services/{svc}/roles` | Список определённых ролей |
| `POST` | `/services/{svc}/roles` | Создать новую роль |
| `PATCH` | `/services/{svc}/roles/{role}` | Обновить описание роли |
| `DELETE` | `/services/{svc}/roles/{role}` | Удалить роль → авто-отзыв у всех пользователей и групп |
| `POST` | `/services/{svc}/roles/{role}/assign` | **Массовая** выдача роли пользователям |
| `POST` | `/services/{svc}/roles/{role}/revoke` | **Массовый** отзыв роли |

Массовое назначение:
```json
POST /services/config_service/roles/reader/assign
{ "user_ids": ["usr_1", "usr_2", "usr_3"] }
```

### Группы (`/groups`)

Кто может управлять группами: `account_admin`. Добавлять участников: `account_admin` или `department_admin` (только пользователей своего отдела).

| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/groups` | Список групп |
| `POST` | `/groups` | Создать группу |
| `PATCH` | `/groups/{id}` | Обновить группу |
| `DELETE` | `/groups/{id}` | Удалить группу |
| `GET/POST` | `/groups/{id}/members` | Список участников / добавить |
| `DELETE` | `/groups/{id}/members/{user_id}` | Убрать участника |
| `GET/POST` | `/groups/{id}/services` | Сервисы группы / выдать доступ |
| `DELETE` | `/groups/{id}/services/{svc}` | Отозвать доступ к сервису |
| `GET/POST` | `/groups/{id}/roles` | Роли группы / назначить роли на сервис |
| `DELETE` | `/groups/{id}/roles/{svc}` | Снять все роли группы на сервис |

Пример: выдать группе доступ к сервису и роль `operator`:
```json
POST /groups/grp_abc/services
{ "service_name": "config_service" }

POST /groups/grp_abc/roles
{ "service_name": "config_service", "roles": ["operator"] }
```

### Personal Access Tokens (`/tokens`)

| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/tokens` | Свои PAT (без значений токенов) |
| `POST` | `/tokens` | Создать PAT (значение возвращается один раз) |
| `DELETE` | `/tokens/{id}` | Отозвать PAT |

### Боты (`/bots`)

| Метод | URL | Кто может | Описание |
|-------|-----|-----------|----------|
| `GET` | `/bots` | AccountAdmin / DeptAdmin | Список ботов (DeptAdmin видит только свой отдел) |
| `POST` | `/bots` | AnyAdmin | Создать бота |
| `POST` | `/bots/{id}/tokens` | AnyAdmin | Выпустить токен для бота |
| `DELETE` | `/bots/{id}/tokens/{token_id}` | AnyAdmin | Отозвать токен бота |

### Авторизация (`/authorization`)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/authorization/introspect` | Валидировать токен → вернуть identity context |
| `POST` | `/authorization/service-access` | Проверить, есть ли у токена доступ к сервису |

Интроспекция принимает JWT, PAT или bot-токен — возвращает единый формат.

---

## Токены

### Жизненный цикл JWT

```
POST /login → access_token (JWT, ~15 мин) + refresh_token (opaque)
  ↓
POST /refresh → новый access_token + новый refresh_token (ротация)
  ↓
POST /logout → отзыв refresh_token (invalidate сессии)
```

Повторное использование refresh-токена (reuse) расценивается как компрометация: все сессии пользователя принудительно закрываются.

### PAT

Используется там, где нужен долгосрочный токен (CI/CD, скрипты). Права PAT определяются правами пользователя на момент обращения — не на момент создания.

### Bot tokens

Боты привязаны к отделу, имеют собственный список `allowed_services`. Используются как пароль в Basic Auth для Docker registry.

---

## Интроспекция и проверка доступа

Все остальные сервисы платформы проверяют права через `auth_service`:

```
POST /authorization/introspect
{ "token": "<jwt или pat или bot-token>" }

→ {
    "active": true,
    "subject_type": "user",        // или "bot"
    "sub": "usr_abc123",
    "department_id": "dep_xyz",
    "allowed_services": ["config_service"],
    "service_roles": { "config_service": ["reader"] },
    "exp": 1745000000
  }
```

```
POST /authorization/service-access
{ "subject_token": "...", "service_name": "config_service" }

→ { "allowed": true, "service_roles": ["reader"] }
```

---

## Боты

Бот (`BotAccount`) — сервисная учётная запись, привязанная к отделу. Используется для автоматизации без привязки к конкретному человеку.

- Бот имеет список `allowed_services`.
- Бот может иметь несколько именованных токенов (`dbos_bot_...`).
- Токен бота проходит интроспекцию и возвращает `subject_type: "bot"`.
- Токен бота работает как пароль в Basic Auth для Docker registry.

---

## Docker registry

`auth_service` реализует протокол аутентификации Docker registry (Bearer token scheme):

```
GET /docker/token  (Basic Auth: username:password)
  password = пароль пользователя | PAT | bot-token
  
→ { "token": "<RS256 JWT>", "access_token": "...", "expires_in": 300, "issued_at": "..." }
```

Права на pull/push определяются конфигурацией реестра отдела (`/docker/registry/{dept_id}`):
- `pull_policy: "all"` — pull разрешён всем пользователям отдела.
- `pull_policy: "restricted"` — pull только для явно указанных пользователей.
- `push_user_ids` — только эти пользователи могут push.

Публичный ключ для верификации токенов реестра:
- `GET /docker/certs` — PEM
- `GET /docker/jwks` — JWKS (RS256)

---

## OAuth2

`auth_service` поддерживает базовый OAuth2 для интеграции с внешними приложениями:

| Grant type | Описание |
|------------|----------|
| `authorization_code` | Пользователь авторизует приложение через redirect |
| `client_credentials` | Сервис-к-сервису (M2M) без участия пользователя |

Управление клиентами: `POST /oauth2/clients` (AccountAdmin или DeptAdmin своего отдела).

---

## Запуск и развёртывание

### Локально (docker-compose)

```bash
cd auth_service
docker-compose up --build
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### Напрямую (требуется PostgreSQL)

```bash
cd auth_service
cp .env.example .env   # задать DATABASE_URL, SECRET_KEY и т.д.
PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Переменные окружения

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/db` |
| `SECRET_KEY` | Секрет для подписи JWT (≥32 символов) |
| `ACCESS_TOKEN_TTL_MINUTES` | Время жизни access-токена (по умолчанию 15) |
| `REFRESH_TOKEN_TTL_DAYS` | Время жизни refresh-токена (по умолчанию 14) |

### Миграции (Alembic)

```bash
cd auth_service
PYTHONPATH=. alembic upgrade head
# Новая миграция после изменения моделей:
PYTHONPATH=. alembic revision --autogenerate -m "описание"
```

### Тесты

```bash
cd auth_service
# Запуск всех тестов
poetry run pytest

# Конкретный файл
poetry run pytest tests/groups/test_groups.py -v

# С логами
poetry run pytest -s --tb=short
```

Тесты используют реальную PostgreSQL (через SAVEPOINT-откат — каждый тест изолирован, схема пересоздаётся один раз на сессию). E2E тесты (`tests/e2e/`) требуют полного docker-compose стека.

### Kubernetes

Манифесты в `k8s/`. Deployment: 2 реплики, readiness → `/api/auth/v1/ready`, liveness → `/api/auth/v1/health`. Секреты и конфиг инжектируются через `secretRef` / `configMapRef`.

---

## Технологии

- Python 3.12, FastAPI, SQLAlchemy 2.0 (async), psycopg3
- PostgreSQL, Alembic
- PyJWT, argon2-cffi, cryptography (RS256 для Docker registry)
- pytest, pytest-asyncio, httpx
- Docker, Kubernetes
