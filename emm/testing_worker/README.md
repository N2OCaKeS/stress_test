# testing_worker

Taskiq + Redis воркер для `testing_service` — отдельный top-level сервис, по
образцу `server_worker` относительно `server_service` (два независимых
пакета, каждый со своим `pyproject.toml`/`Dockerfile`/`Makefile`, не общий
код с двумя entrypoint'ами).

## Статус

Реальный SSH-исполнитель (волна 5 плана миграции, `emm/obsidian/ALLTA
MIGRATION.md`, §5/§5.5). Работа идёт не через taskiq task-handler'ы —
диспетчер один: очередь `testing_service`. На `WORKER_STARTUP` поднимается
фоновый asyncio-loop (`src/services/queue_loop.py`), который в цикле:

1. забирает готовое задание (`POST /internal/queue/claim`);
2. подключается к стенду по SSH под тестовыми кредами и исполняет
   резолвленную команду (`src/services/ssh_executor.py`, `asyncssh`);
3. сообщает исход (`POST /internal/queue/{id}/completed`).

`system.ping` остаётся как смоук-задача (не убран — не мешает, оставлен на
случай если понадобится проверить, что broker вообще слушает очередь).

В отличие от `server_worker`, у `testing_worker` пока **нет собственной БД** —
своя PostgreSQL (`db/`, `models/`, `alembic.ini`) появится вместе с первыми
durable-задачами, если понадобится outbox по образцу `server_worker`
(кандидат — sweep зависших `running`-item'ов, см. отчёт волны).

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
  main.py           # broker + logging setup + WORKER_STARTUP/SHUTDOWN хуки polling-loop'а
  core/
    broker.py        # taskiq ListQueueBroker + Redis result backend
    config.py         # Settings: REDIS_URL, TASKIQ_QUEUE_NAME, TESTING_SERVICE_URL/KEY, таймауты SSH, poll interval
    constants.py       # SERVICE_NAME = "testing_worker" (X-Service-Identity)
    http.py             # bearer_header() хелпер
    logging.py        # JSON-структурированное логирование (копия testing_service/src/core/logging.py)
  services/
    queue_loop.py      # run_polling_loop() — claim → execute → report_completed, без сна между item'ами
    testing_client.py  # POST /internal/queue/claim, /internal/queue/{id}/completed
    ssh_executor.py     # asyncssh-исполнение резолвленной команды (shlex.join, без shell=True)
  tasks/
    __init__.py       # пустой пакет — задач-taskiq нет, диспетчер — очередь testing_service
```

## Конфигурация (env-vars)

| ENV | Default | Назначение |
|---|---|---|
| `APP_ENV` | `local` | метка окружения (`local`\|`dev`\|`test`\|`staging`\|`production`) |
| `REDIS_URL` | `redis://redis:6379/3` | Redis URL для taskiq-брокера, тот же db-index, что у `testing_service` |
| `TASKIQ_QUEUE_NAME` | `testing_taskiq` | имя очереди taskiq (Redis-список) |
| `WORKER_LOG_LEVEL` | `INFO` | python log level |
| `TESTING_SERVICE_URL` | — | базовый URL `testing_service` (например `http://testing_service:8004`) |
| `TESTING_SERVICE_INTERNAL_API_KEY` | — | shared-secret для `/internal/queue/*`, совпадает с `SERVICE_API_KEYS['testing_worker']` на `testing_service` |
| `QUEUE_POLL_INTERVAL_SECONDS` | `3.0` | пауза между `claim`, когда очередь пуста/`testing_service` недоступен |
| `INTERRUPT_POLL_INTERVAL_SECONDS` | `7.0` | как часто спрашивать `interrupt-check`, пока тест выполняется |
| `SSH_CONNECT_TIMEOUT_SECONDS` | `30.0` | таймаут TCP-коннекта + SSH-handshake + аутентификации |
| `SSH_COMMAND_TIMEOUT_SECONDS` | `3600.0` | грубый общий cap на исполнение команды теста |

## Тесты

```bash
make test        # из testing_worker/, через tests/docker-compose.test.yml
```

Полностью замоканы — без реального SSH-сервера и без `testing_service`:
`test_broker.py` — структура broker'а; `test_testing_client.py` — claim/
completed через `httpx.MockTransport`; `test_ssh_executor.py` — `asyncssh`
подменяется на уровне модуля (успех/провал команды/провал коннекта/таймаут) +
`shlex.join` roundtrip на инъекционно-опасных аргументах; `test_queue_loop.py`
— один проход цикла (item есть/очередь пуста/неожиданная ошибка).
