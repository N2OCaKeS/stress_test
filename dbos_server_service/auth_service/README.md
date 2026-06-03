# auth_service

Центральный сервис аутентификации и авторизации платформы DBOS Server Manager.

Все остальные сервисы (`server_service`, `loging_service`, `config_service`) доверяют только `auth_service` в вопросах того, кто есть кто и что кому разрешено.

---

## Что делает сервис

- Аутентификация пользователей (username/password → JWT + refresh).
- Personal Access Tokens (`dbos_pat_…`) — долгосрочные токены пользователей.
- Bot accounts + bot-tokens (`dbos_bot_…`) — service-accounts для автоматизации.
- OAuth2 (authorization_code с PKCE + client_credentials).
- Docker Registry token-auth (RS256 JWT).
- Introspect токенов для других сервисов (`POST /authorization/introspect`).
- Каталог ролей: platform-роли + service-роли в scope `(department, service)`.

---

## Архитектура

Сервис разложен на 3 слоя:

```text
HTTP request
  → src/api/v1/endpoints/*.py   (FastAPI routers — только request/response)
  → src/services/*.py           (бизнес-логика, инварианты, аудит)
  → src/repositories/*.py       (SQLAlchemy запросы)
  → src/models/*.py             (ORM)
```

Pydantic-схемы в `src/schemas/` — отдельная плоскость от ORM. Зависимости (`get_current_identity`, `require_account_admin`, `require_any_admin`, `require_service_token`, `get_db`) живут в `src/dependencies/`.

---

## Категории ролей

Сервис различает 5 классов идентичностей. Чёткое деление нужно, чтобы платформенный администратор не превращался в дополнительные права в прикладных сервисах.

### Platform-роли (`PlatformRole` enum)

| Роль | Что может | `department_id` |
|------|-----------|------|
| `account_admin` | Глобальный платформенный администратор. Управляет сервисами, отделами, группами, OAuth2-клиентами, может банить/разбанить юзеров. **НЕ** получает прикладных service-ролей — `allowed_services` в introspect всегда пустой (`authorization_service.py:172`). | NULL разрешён |
| `department_admin` | Администратор одного отдела. Управляет юзерами, ботами, группами, OAuth2-клиентами своего отдела. На чужой отдел — 404 / 403 (cross-dept enumeration prevention). | обязателен |
| `loging_admin` | Полный доступ в `loging_service` (управление правилами severity/suppress + retention + чтение каталога аудита по всем отделам). В `auth_service` собственных прав не имеет. | NULL разрешён |
| `loging_reader` | Read-only доступ в `loging_service` ко всем отделам (мониторинг, без управления правилами). В `auth_service` собственных прав не имеет. | NULL разрешён |
| service-role (отсутствие platform-роли) | Обычный юзер. Права — через прямые `UserServiceRole`, группы (`GroupServiceRole`) и `DepartmentServiceAccess`. | обязателен |

Все четыре значения — `PlatformRole` enum (`core/constants.py`): `ACCOUNT_ADMIN`, `DEPARTMENT_ADMIN`, `LOGING_ADMIN`, `LOGING_READER`. Bit-flagged роли вроде `departments`/`group_admin` платформа не использует — управление этими сущностями завязано на `account_admin` (глобально) и `department_admin` (внутри своего отдела), см. таблицу выше.

Допуск `NULL department_id` для `ACCOUNT_ADMIN`/`LOGING_ADMIN`/`LOGING_READER` — в `user_service.py:_platform_admins`. Для обычных юзеров отсутствие отдела → `MISSING_REQUIRED_FIELD`. `loging_reader` без `department_id` на стороне `auth_service` создаётся успешно; политика «dept-scoped read» сейчас держится только на стороне `loging_service` (без `department_id` он отдаст `403 NO_DEPARTMENT` на первом же GET аудита). Платформенные admin'ы могут иметь service-роли только для `LOGING_*` (бизнес-смысл — присматривать за loging_service), но guard'ы прикладных сервисов это игнорируют: `account_admin` bypass-ит action-матрицу `server_service`, остальные platform-роли там прав не получают.

### Service-роли

Динамические, определяются для каждой пары `(department, service)` через `ServiceRoleDefinition`. При создании платформенного сервиса автоматически создаётся системная роль `admin` (`is_system=True`, не удаляется).

Эффективные права = `direct UserServiceRole ∪ GroupServiceRole`, INTERSECT с `DepartmentServiceAccess ∪ GroupServiceAccess`. Если отдел потерял доступ к сервису, роли пользователей для этого сервиса автоматически выпадают из effective view (`_merge_permissions` отбрасывает их без удаления из БД).

---

## Identity Context

Каждый access token (JWT) содержит:

```json
{
  "sub": "usr_abc123",
  "actor_type": "user",
  "iat": 1747000000,
  "exp": 1747000600,
  "iss": "auth_service",
  "aud": "dbos-platform"
}
```

**Чувствительные claims** (`is_banned`, `allowed_services`, `service_roles`, `platform_role`, `department_id`) в JWT **не лежат**. На каждом запросе `get_current_identity`/`introspect` перечитывает их из БД через `user_repo.get_by_id(sub)` + `collect_user_permissions`. Это позволяет немедленно отзывать доступ — забаненный юзер не пройдёт даже с валидной подписью свежего JWT.

`client_credentials`-JWT (m2m, `actor_type=oauth_client`) минимизирован симметрично user-JWT: в payload только `sub` (= `client_id`), `actor_type` и `oauth_client_id` плюс стандартные `iat`/`exp`/`iss`/`aud`. `department_id`/`allowed_services`/`service_roles` в payload не кладутся — эффективный `scope` уходит в OAuth-ответ и audit, а права клиентские сервисы перечитывают через introspect.

Полный identity отдаёт `GET /me` и `POST /authorization/introspect`:

```json
{
  "user_id": "usr_abc123",
  "username": "ivanov",
  "department_id": "dep_xyz",
  "department_name": "НТ",
  "platform_role": null,
  "allowed_services": ["config_service", "server_service"],
  "service_roles": {
    "config_service": ["reader", "operator"],
    "server_service": ["reader"]
  },
  "is_banned": false
}
```

Для всех типов субъектов (user-JWT, PAT, bot) `allowed_services`/`service_roles` собираются единым `collect_user_permissions` — учитывается как прямой dept-access, так и сервисы, доступные через группы. PAT при introspect видит group-derived service access симметрично user-JWT и `/me`; собственный scope PAT (`allowed_services`) при этом только сужается до реально доступного юзеру набора, прав не добавляет.

---

## Безопасность

- **Пароли** — Argon2id с OWASP 2023 параметрами (`time_cost=3, memory_cost=64 MiB, parallelism=4`).
- **JWT** — короткоживущий (10 мин по умолчанию), `iat`/`exp`/`iss`/`aud` + leeway. Подписывается `SECRET_KEY` (HS256) или RSA (`DOCKER_RSA_PRIVATE_KEY`, RS256) для Docker-токенов. Чувствительные claims не лежат в payload.
- **Refresh** — opaque random secret, в БД только SHA-256 hash. Ротация через `SessionRepository.rotate` (CAS на `previous_token_hash` + `token_generation`). Reuse детектируется и убивает всю сессию пользователя (kill-switch).
- **PAT / bot-токены** — opaque, prefixed (`dbos_pat_…`, `dbos_bot_…`), только hash в БД, raw показывается один раз. `ban_user` revoke'ит все активные PAT и bot-токены owned-ботов пользователя.
- **Lockout** — общий pipeline для `/login` и `/docker/token`: 5 неудач → 15 мин lockout, ответ 429 с `retry_after_seconds`. Inkrement через атомарный `UPDATE...RETURNING` с commit'ом до raise, иначе rollback откатил бы счётчик.
- **dept-isolation** — `department_admin` видит и меняет только свой отдел. Cross-dept enumeration защищён 404'ом вместо 403 (нет ID oracle). `account_admin` cross-dept by design. `_merge_permissions` INTERSECT отбрасывает роли для сервисов, которые отдел больше не имеет.
- **OAuth2** — PKCE опционален для confidential client'ов, обязателен для public; `redirect_uri` валидируется (https или http://localhost, без fragment); `response_type` ограничен `code` (implicit и hybrid запрещены); m2m JWT (`actor_type=oauth_client`) отбивается `require_user_context` guard'ом с user-facing endpoints.
- **Rate-limit (slowapi)** — на `/login`, `/refresh`, `/docker/token`, `/authorization/introspect`.
- **Security headers + CORS** — middleware вкручен (HSTS, X-Content-Type-Options, Referrer-Policy и т.д.).
- **Service-to-service** — `/authorization/*` закрыты `SERVICE_API_KEY` + опциональный `X-Service-Identity` (soft / strict через `STRICT_SERVICE_IDENTITY`). `SERVICE_API_KEYS` JSON env даёт per-service ключи в дополнение к shared.
- **Production-guards** — `_validate_production_secrets` отбивает `change-me`-substring placeholder'ы, требует длину секретов ≥32, обязательные `LOGGING_SERVICE_API_KEY`/`DOCKER_RSA_PRIVATE_KEY`, и https-схему для `LOGGING_SERVICE_URL` в prod.
- **Identity TTL-кэш** — `get_current_identity` кэширует identity на короткий срок по Bearer-токену, чтобы не дёргать БД на каждом запросе. Кэш не маскирует revoke — TTL короче access TTL. Кэш bounded (LRU-вытеснение по `maxsize`), не растёт безгранично.
- **Audit на HTTP-исходах** — `http.access_denied`/`http.client_error`/`http.server_error` несут actor_id/username/ip/ua/request_id (middleware-порядок выставлен так, что контекст жив на момент emit). Rate-limit 429 при этом не плодит `http.client_error`.

---

## API — обзор

Все endpoints под `/api/auth/v1/`. Полный каталог с примерами и error codes — в [API_ENDPOINTS.md](API_ENDPOINTS.md). Swagger: `http://localhost:8000/docs`.

List-эндпоинты `GET /users`, `/users/department/{id}`, `/bots`, `/groups` пагинируются query-параметрами `limit` (default 50, max 200) и `offset`; тело остаётся списком, полное число записей — в заголовке `X-Total-Count`.

### Категории

| Группа | Префикс | Назначение |
|------|------|------|
| auth | (root) | login, refresh, logout, me, health, ready |
| users | `/users` | CRUD юзеров, роли, ban/unban, группы, permissions snapshot |
| departments | `/departments` | отделы + grant/revoke service-access |
| services | `/services` | регистр платформенных сервисов |
| service_roles | `/departments/{dept_id}/services/{service_name}/roles` | `ServiceRoleDefinition` per (dept, service) + bulk assign/revoke |
| tokens | `/tokens` | Personal Access Tokens |
| bots | `/bots` | bot-accounts, bot-tokens, bot-service-roles |
| groups | `/groups` | группы + члены + service-access + service-roles |
| authorization | `/authorization` | introspect + service-access (service-to-service) |
| oauth2 | `/oauth2` | OAuth2 clients + authorize + token |
| docker | `/docker` | Docker Registry token-auth + per-dept config |

### Ключевые особенности

- **`POST /users/{id}/roles`** — replace-семантика для пары (user, service): новый список ролей полностью заменяет старый. Пустой список = снять все.
- **`GET /users/{id}/permissions`** — полный снимок: прямые роли + группы (со всеми их service-access и service-roles) + effective view (merged + INTERSECT) + статус. Для админ-UI с указанием источника каждой роли.
- **`/departments/{dept_id}/services/{service_name}/roles/{role}/assign|revoke`** — bulk-операции по списку `user_ids`.
- **`/groups`** — может создавать `account_admin` (любой отдел) или `department_admin` (только свой). Group замкнута на department: член группы и сама группа всегда в одном отделе (`GROUP_DEPARTMENT_MISMATCH`).
- **`/oauth2/clients`** — создание клиента: `account_admin` (любой отдел) или `department_admin` (свой отдел).
- **`/docker/registry/{dept_id}`** (CRUD конфиг) — `account_admin` или `department_admin` своего отдела. PUT — replace, PATCH — частичный.
- **`/docker/token`** — Basic auth (`username:password` / `username:dbos_pat_…` / `botname:dbos_bot_…`) → RS256 JWT со scope'ами `repository:<name>:pull/push`.

---

## Запуск

### Локально (docker-compose)

```bash
# Из корня dbos_server_service — общий dev-стек (auth + logging + server + worker):
make up    # см. корневой Makefile
# API:   http://localhost:8000
# Docs:  http://localhost:8000/docs

# Либо отдельный compose только для auth_service:
docker compose -f auth_service/docker/docker-compose.yml up --build
```

### Напрямую (требуется PostgreSQL)

```bash
cd auth_service
cp .env.example .env   # задать DATABASE_URL, SECRET_KEY и т.д.
PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Переменные окружения (ключевые)

| Переменная | Описание |
|------|------|
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/db` |
| `DB_POOL_SIZE` | SQLAlchemy pool_size (default 10) |
| `DB_MAX_OVERFLOW` | SQLAlchemy max_overflow (default 20) |
| `SECRET_KEY` | JWT подпись (≥32 символов, не `change-me` в prod) |
| `ACCESS_TOKEN_TTL_MINUTES` | TTL access JWT (default 10) |
| `REFRESH_TOKEN_TTL_DAYS` | TTL refresh (default 14) |
| `JWT_AUDIENCE`, `JWT_ISSUER`, `JWT_LEEWAY_SECONDS` | hardening |
| `DOCKER_RSA_PRIVATE_KEY` | PEM RSA для RS256 Docker JWT (обязателен в prod) |
| `LOGGING_SERVICE_URL` | https в prod (https-guard) |
| `LOGGING_SERVICE_API_KEY` | Bearer для loging_service (обязателен в prod) |
| `SERVICE_API_KEY` | shared secret для introspect (обязателен в prod) |
| `SERVICE_API_KEYS` | JSON env с per-service ключами (опционально, dual-mode) |
| `STRICT_SERVICE_IDENTITY` | bool, default False — strict-режим `X-Service-Identity` |
| `TRUSTED_PROXY_IPS` | CIDR list для XFF (default `[]`) |
| `RATE_LIMIT_STORAGE_URI` | slowapi backend (например `redis://host:6379/0`); fallback `memory://` |
| `INITIAL_ADMIN_USERNAME/PASSWORD/EMAIL` | одноразовый bootstrap |

`.env.example` показывает базовый bootstrap (DB, JWT, registry, admin). Полный набор env-vars — в `src/core/config.py` (Settings); каждая опция там с docstring и default'ом.

### Rate-limit storage

slowapi считает per-IP попытки в backend'е, который задаётся через
`RATE_LIMIT_STORAGE_URI`. По умолчанию backend — `memory://` (per-process
in-memory счётчик), который годится для dev/test и одиночного uvicorn'а. В
K8s, где `auth_service` обычно крутится в 2+ репликах, in-memory backend
ломает per-IP лимит: round-robin раскладывает попытки по pod'ам, каждый
держит свой счётчик, и brute-force получает фактически `N × LOGIN_RATE_LIMIT`
попыток за окно. Per-user lockout (`failed_login_attempts` в БД) при этом
работает, но username-rotating атака обходит его за per-IP бюджет.

Для production укажи общий backend: `RATE_LIMIT_STORAGE_URI=redis://host:6379/0`
(либо unix-socket / memcached / любой другой backend из списка `limits`).
Lifespan auth_service на старте пишет в лог `rate_limit_storage: <uri>` (без
пароля) и WARN'ит, если в prod выбран `memory://`.

### Миграции (Alembic)

```bash
cd auth_service
PYTHONPATH=. alembic upgrade head
# Новая миграция после изменения моделей:
PYTHONPATH=. alembic revision --autogenerate -m "описание"
```

### Тесты

```bash
# Из корня dbos_server_service:
make test-auth         # быстрые в Docker (TestClient + postgres-тест)
make test-auth-e2e     # все, включая e2e (registry stack)
make test-dev-auth     # быстрые в devcontainer
```

Тесты используют реальный PostgreSQL (через SAVEPOINT-откат — каждый тест изолирован, схема пересоздаётся один раз на сессию). E2E (`tests/e2e/`) требуют полного docker-compose стека с `registry:2` и `fetch-cert`.

### Kubernetes

Манифесты в `k8s/`. Deployment: 2 реплики, readiness → `/api/auth/v1/ready`, liveness → `/api/auth/v1/health`. Секреты и конфиг — `secretRef` / `configMapRef`.

---

## Технологии

- Python 3.12, FastAPI, SQLAlchemy 2.0 (async), psycopg3
- PostgreSQL 16, Alembic
- PyJWT, argon2-cffi, cryptography (RS256 для Docker JWT)
- slowapi (rate-limit), pydantic-settings
- pytest, pytest-asyncio, httpx
- Docker, Kubernetes
