# testing_service · статус реализации

Источник истины по архитектуре — `emm/obsidian/ALLTA MIGRATION.md`. Этот файл — короткий срез «что уже есть в коде» по волнам §16 того плана.

## Волна 1 — каркас сервиса (текущая)

**Сделано:**

- `api` (FastAPI): `/api/testing/v1/health`, `/api/testing/v1/ready`, стандартный error-envelope (`AppException`/`RequestValidationError`/`IntegrityError` handlers), `SecurityHeadersMiddleware`, `HTTPSRequiredMiddleware` (prod TLS-guard), `AuditAccessMiddleware` (http.* события), slowapi rate-limit, self-host Swagger UI.
- Auth-инфраструктура: introspect в `auth_service` (`CurrentIdentity`/`CurrentUserIdentity`), `require_internal_caller` для будущего s2s-канала (server_service → testing_service callback).
- Audit-паблишер: `audit_service.emit()` → durable outbox (`audit_outbox`) → фоновый дренаж в `loging_service` (`services/audit_outbox_publisher.py`, порт паттерна из `server_worker`); каталог событий (`service.started` + `http.*`), редактирование секретов в details. At-least-once, per-row backoff, DLQ по cap'у попыток / permanent 4xx / битому payload'у.
- `worker` — вынесен в отдельный top-level сервис `../testing_worker` (свой `pyproject.toml`/`Dockerfile`/`Makefile`, по образцу `server_worker`); пустой task-registry, broker слушает `testing_taskiq` очередь на Redis db `/3`. Своей БД у воркера пока нет.
- Alembic wiring (`alembic.ini`, `src/db/migrations/env.py`).
- Docker/compose/k8s/Makefile wiring — сервис поднимается в общем dev-стеке и тестовом docker-compose.

## Волна 3 — глобальные переменные (§2.1, §3.3, §3.5)

**Сделано:**

- Модель + CRUD `global_variables` (`GET/POST/PATCH/DELETE /api/testing/v1/global-variables`, плюс `/by-code/{code}`). Каталог платформенный, чтение — любому аутентифицированному актору, запись — под матрицей прав.
- Ролевая инфраструктура сервиса: `EntityType`/`Action`/`ENTITY_ACTIONS` в `core/constants.py`, таблица `entity_permissions` (структура 1:1 с `server_service`/`secret_service`), read-side хелперы `services/permissions.py`. Системной роли `admin` засеяны `view/create/update/delete` на `global_variable`.
- Сиды обязательных переменных: `RC`, `STAND`, `KERNEL`, `MODE`, `TESTENV`, `HOME_DIR`, `TEST_USER`, `TEST_PASSWORD`, `TEST_SSH_KEY` (последние две — `is_sensitive`).
- Резолверы `choices_source`: `static:<json>`, `dynamic:os_versions`, `dynamic:kernels` (реестр `RESOLVERS` в `services/choices.py`), эндпоинт `GET /global-variables/{id}/choices` — резолв в момент отображения, не при сохранении.
- Исходящий канал в `server_service` (`services/server_client.py`, `SERVER_SERVICE_URL` + `SERVER_SERVICE_API_KEY`).
- Миграция `b1e7c4a9d203` (первая в сервисе).

**Не сделано:** `dynamic:department_credential` (§3.5) — вместе с исполнением тестов; CRUD самой матрицы прав (grant/revoke эндпоинты) — когда понадобятся кастомные роли отдела.

**Не сделано (следующие волны):**

- Волна 2 (`server_service`): каталог категорий стендов, `prepare-for-test`, `busy_state=testing` — **уже реализовано на стороне `server_service`** в этой же сессии (см. `ALLTA MIGRATION.md` §0.1), но `testing_service` пока не вызывает эти эндпоинты.
- Волна 4-12: стенды, SSH-worker, логи, прогоны, СТП/Zephyr/Confluence, changelog, department-отчёты, фронтенд cutover, импорт легаси-данных.

## Тесты

Смоук-тест `/health`+`/ready` в `tests/test_health.py`, гоняется через `tests/docker-compose.test.yml` (реальный Postgres+Redis). Подробности — `Test.md`.
