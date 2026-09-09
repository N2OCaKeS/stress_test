# testing_worker

Taskiq + Redis воркер для `testing_service` — отдельный top-level сервис, по
образцу `server_worker` относительно `server_service` (два независимых
пакета, каждый со своим `pyproject.toml`/`Dockerfile`/`Makefile`, не общий
код с двумя entrypoint'ами).

## Статус

Каркасная волна: broker + logging + пустой task-registry (`src/tasks/`),
чтобы `taskiq worker` реально стартовал и держал соединение с Redis. Никакой
бизнес-логики пока нет — первые задачи (свой SSH-worker, исполнение теста под
кредами из `server_service`-`prepare-for-test`) появятся в волне 5 плана
миграции (`emm/obsidian/ALLTA MIGRATION.md`, §5).

В отличие от `server_worker`, у `testing_worker` пока **нет собственной БД** —
задач в реестре ещё нет, писать/читать нечего. Своя PostgreSQL (`db/`,
`models/`, `alembic.ini`) появится вместе с первыми durable-задачами, если
понадобится outbox по образцу `server_worker`.

## Stack

- Python 3.12, asyncio
- [taskiq](https://taskiq-python.github.io/) + `taskiq-redis` — async task queue, Redis broker
- Общий Redis-контейнер с остальными сервисами, свой db-index (`/3`, симметрично `testing_service`)

## Running locally

```bash
cd ..              # to emm/
make up            # весь dev-стек, включая testing_worker
make logs-testing  # tail логов testing_service + testing_worker
make test-testing-worker   # прогон тестов воркера в Docker
```

Локальный foreground-запуск (devcontainer):

```bash
make run-testing-worker
```

## Architecture map

```
src/
  main.py           # taskiq broker + logging setup + entrypoint (`taskiq worker src.main:broker`)
  core/
    broker.py        # taskiq ListQueueBroker + Redis result backend
    config.py         # Settings: REDIS_URL, TASKIQ_QUEUE_NAME, WORKER_LOG_LEVEL, APP_ENV
    logging.py        # JSON-структурированное логирование (копия testing_service/src/core/logging.py)
  tasks/
    __init__.py       # пустой пакет — задачи появятся в волне 5
```

## Конфигурация (env-vars)

| ENV | Default | Назначение |
|---|---|---|
| `APP_ENV` | `local` | метка окружения (`local`\|`dev`\|`test`\|`staging`\|`production`) |
| `REDIS_URL` | `redis://redis:6379/3` | Redis URL для taskiq-брокера, тот же db-index, что у `testing_service` |
| `TASKIQ_QUEUE_NAME` | `testing_taskiq` | имя очереди taskiq (Redis-список) |
| `WORKER_LOG_LEVEL` | `INFO` | python log level |

## Тесты

```bash
make test        # из testing_worker/, через tests/docker-compose.test.yml
```

Смоук-тест (`tests/test_broker.py`) проверяет структуру, а не поведение:
broker импортируется, `system.ping` зарегистрирована на нём — без реального
Redis-соединения (по аналогии с тем, как `testing_service/tests/test_health.py`
проверяет собранность API-скелета).
