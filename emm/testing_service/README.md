# testing_service

## Назначение

`testing_service` — перенос функционала легаси `allta_app` на конвенции emm: каталог тестов, стенды, очередь запуска, конструктор команд, СТП (Zephyr Scale), интеграция с Confluence, changelog-фильтрация, логи прогонов, department-отчёты. Полный план миграции — `emm/obsidian/ALLTA MIGRATION.md`.

**Текущий статус — каркас плюс первые доменные каталоги** (что именно готово — `STATUS.md`). Воркер-задач пока нет. Этот README описывает то, что реально есть в коде; по мере реализации волн (`obsidian/ALLTA MIGRATION.md` §16) секции наполнятся.

## Компоненты

- **`api`** (`src/main.py`) — FastAPI-приложение, `/api/testing/v1/health` + `/api/testing/v1/ready`. Слоистая структура `api → services → repositories → models` заводится по мере появления домена.
- **`worker`** (`src/worker_main.py`) — отдельный процесс, свой taskiq+redis broker (`src/core/broker.py`). Пока без реальных задач — собственный SSH-worker появится в волне 5.
- **`postgres`** — `testing_db`. Миграций пока 0 — модели появятся вместе с доменом (волна 3+).
- **`redis`** — db-index `/3` на общем redis-контейнере (`/0` — `server_worker` broker, `/1` — `auth_service` rate-limit в prod, `/2` — `secret_service` rate-limit; `/1` занят только в prod-стеке, но нумерация держится единой между dev/prod, чтобы не путаться).
- **Auth** — через `auth_service` (introspect). **Audit** — публикация в `loging_service`, инфраструктура готова, доменных событий пока нет.

## Технологии

- `Python 3.12`
- `FastAPI` + `SQLAlchemy 2.0 async` + `Alembic`
- `taskiq` + `taskiq-redis` (worker)
- `PostgreSQL` (отдельный кластер), `Redis`
- `Docker`, `Kubernetes`

## Healthcheck

Два публичных endpoint'а под k8s probe'ы (без auth, без rate-limit).

### `GET /api/testing/v1/health` — liveness

Сервис жив, процесс не повис. Не трогает БД, не зовёт зависимости.

```json
{ "status": "ok", "timestamp": "2026-09-09T12:34:56.000+00:00" }
```

### `GET /api/testing/v1/ready` — readiness

БД обязательна для зелёного `status=ok`; иначе `status=degraded` (HTTP всё равно 200). Redis (taskiq-брокер testing_worker'а) — best-effort, фейл не валит ready.

```json
{
  "status": "ok",
  "timestamp": "2026-09-09T12:34:56.000+00:00",
  "db": true,
  "redis_connected": true,
  "audit_dropped_429_total": 0,
  "counters": { "audit_dropped_429": 0 }
}
```

## Локальный запуск

Через корневой `Makefile` (см. `make help`):

```bash
make run-testing          # api на :8004, foreground, авто-перезагрузка
make run-testing-worker   # taskiq worker, foreground
make test-testing         # тесты в Docker (tests/docker-compose.test.yml)
```

Стандартный dev-стек — `docker-compose.dev.yml` в корне репозитория (`make up`/`make dev`).

## Что НЕ делает сервис (пока)

- Не хранит платформенные mgmt-креды стендов — их держит `server_service` (§5.1 плана миграции).
- Не хранит токены Jira/Confluence/Zephyr — они в `secret_service` (department-scope credential, §2.4).
- Не дублирует ACS/Clonezilla-снимки, ASTRA/ALLTA health хоста, RC/build-каталог — всё это уже в `server_service` (§0 плана миграции).
