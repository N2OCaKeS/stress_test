# testing_service · статус реализации

Источник истины по архитектуре — `emm/obsidian/ALLTA MIGRATION.md`. Этот файл — короткий срез «что уже есть в коде» по волнам §16 того плана.

## Волна 1 — каркас сервиса (текущая)

**Сделано:**

- `api` (FastAPI): `/api/testing/v1/health`, `/api/testing/v1/ready`, стандартный error-envelope (`AppException`/`RequestValidationError`/`IntegrityError` handlers), `SecurityHeadersMiddleware`, `HTTPSRequiredMiddleware` (prod TLS-guard), `AuditAccessMiddleware` (http.* события), slowapi rate-limit, self-host Swagger UI.
- Auth-инфраструктура: introspect в `auth_service` (`CurrentIdentity`/`CurrentUserIdentity`), `require_internal_caller` для будущего s2s-канала (server_service → testing_service callback).
- Audit-паблишер: `audit_service.emit()` → `loging_service`, каталог событий (`service.started` + `http.*`), редактирование секретов в details.
- `worker` (taskiq+redis, отдельный процесс `src/worker_main.py`) — пустой task-registry, broker слушает `testing_taskiq` очередь на Redis db `/1`.
- Alembic wiring (`alembic.ini`, `src/db/migrations/env.py`) — 0 миграций, моделей ещё нет.
- Docker/compose/k8s/Makefile wiring — сервис поднимается в общем dev-стеке и тестовом docker-compose.

**Не сделано (следующие волны):**

- Волна 2 (`server_service`): каталог категорий стендов, `prepare-for-test`, `busy_state=testing` — **уже реализовано на стороне `server_service`** в этой же сессии (см. `ALLTA MIGRATION.md` §0.1), но `testing_service` пока не вызывает эти эндпоинты.
- Волна 3-12: глобальные переменные, каталог тестов, стенды, SSH-worker, логи, прогоны, СТП/Zephyr/Confluence, changelog, department-отчёты, фронтенд cutover, импорт легаси-данных.

## Тесты

Смоук-тест `/health`+`/ready` в `tests/test_health.py`, гоняется через `tests/docker-compose.test.yml` (реальный Postgres+Redis). Подробности — `Test.md`.
