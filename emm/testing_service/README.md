# testing_service

## Назначение

`testing_service` — перенос функционала легаси `allta_app` на конвенции emm: каталог тестов, стенды, очередь запуска, конструктор команд, СТП (Zephyr Scale), интеграция с Confluence, changelog-фильтрация, логи прогонов, department-отчёты. Полный план миграции — `emm/obsidian/ALLTA MIGRATION.md`.

**Текущий статус — каркас плюс первые доменные каталоги** (что именно готово — `STATUS.md`). Воркер-задач пока нет. Этот README описывает то, что реально есть в коде; по мере реализации волн (`obsidian/ALLTA MIGRATION.md` §16) секции наполнятся.

## Компоненты

- **`api`** (`src/main.py`) — FastAPI-приложение, `/api/testing/v1/health` + `/api/testing/v1/ready`. Слоистая структура `api → services → repositories → models` заводится по мере появления домена.
- **`worker`** — отдельный top-level сервис `../testing_worker` (свой `pyproject.toml`/`Dockerfile`/`Makefile`), по образцу `server_worker` относительно `server_service`. Пока без реальных задач — собственный SSH-worker появится в волне 5. У воркера пока нет своей БД.
- **`postgres`** — `testing_db`. Миграций пока 0 — модели появятся вместе с доменом (волна 3+).
- **`redis`** — db-index `/3` на общем redis-контейнере (`/0` — `server_worker` broker, `/1` — `auth_service` rate-limit в prod, `/2` — `secret_service` rate-limit; `/1` занят только в prod-стеке, но нумерация держится единой между dev/prod, чтобы не путаться). `testing_service` сам ходит в Redis только best-effort пингом в `/ready` (и опционально как backend rate-limit'а) — брокер задач слушает `testing_worker`.
- **Auth** — через `auth_service` (introspect). **Audit** — публикация в `loging_service`, инфраструктура готова, доменных событий пока нет.

## Технологии

- `Python 3.12`
- `FastAPI` + `SQLAlchemy 2.0 async` + `Alembic`
- `PostgreSQL` (отдельный кластер), `Redis`
- `Docker`, `Kubernetes`

Воркер (`taskiq` + `taskiq-redis`) — отдельный пакет, см. `../testing_worker/README.md`.

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
make run-testing-worker   # testing_worker (taskiq worker), foreground
make test-testing         # тесты api в Docker (tests/docker-compose.test.yml)
make test-testing-worker  # тесты testing_worker в Docker
```

Стандартный dev-стек — `docker-compose.dev.yml` в корне репозитория (`make up`/`make dev`).

## Импорт каталога тестов и стендов

`scripts/import_catalog.py` — одноразовый инструмент первого наполнения (§13 плана
миграции): читает JSON/YAML-файл с разделами `stands`/`tests` и заводит записи через
обычный сервисный слой (`services/test_stand.py`, `services/test_definition.py`,
`services/test_command_arg.py`) — та же бизнес-логика и аудит, что у HTTP-API, без
параллельного пути записи в БД. Формат файла и комментарии, откуда брать реальные
данные — см. `scripts/import_catalog.example.yaml`.

```bash
cd emm/testing_service
PYTHONPATH=. DATABASE_URL=postgresql+psycopg://... AUTH_SERVICE_URL=... \
    python scripts/import_catalog.py my_catalog.yaml --bearer-token <admin JWT>
```

`--bearer-token` (или `IMPORT_BEARER_TOKEN` в env) нужен только разделу `stands` —
`create_test_stand` резолвит `department_id` живым pass-through вызовом к
`server_service`, обойти это нельзя, не изобретая параллельный путь. Без токена
позиции `stands` пропускаются с понятной ошибкой на каждую, `tests` заводятся как
обычно. `--dry-run` — резолв и проверка идемпотентности (code/server_id уже
существует) без записи в БД и без живых вызовов к `server_service`.

Идемпотентен: повторный запуск на уже наполненной БД пропускает существующие
`code`/`server_id` (skip, не дубль и не падение). Одна плохая позиция (опечатка в
`variable_code`, недостижимый `server_service`, конфликт по коду) не останавливает
обработку остальных — в конце печатается сводка `created`/`skipped`/`failed` с
текстом ошибки на каждый провал.

**Этой волной реальные данные НЕ импортированы намеренно.** 20+2 реальных стенда
требуют настоящих `server_id` из живого `server_service` конкретной инфраструктуры —
их нет в dev-окружении. Реальный каталог тестов (~30 типоспецифичных легаси-флагов
`allta_app/backup_image.py` — `-psql_aud`/`-ipa`/`-vpn`/`-mail`/`-network`/
`-astraevents`/`-olap` и т.д., ветка `dev_allta_app`) требует ручного разбора
семантики каждого флага (что именно исполняется, какие у него реальные аргументы) —
фабриковать это по одним только именам флагов означало бы риск тихо неверно
определённого теста, который обнаружится только на живом стенде. Инструмент готов
принять оба набора данных, когда они появятся — это ручная задача для того, кто
знает реальную семантику каждого теста и имеет доступ к живому `server_service`.

## Копирование параметров в конструкторе

В разделе «Тесты» откройте конструктор текущего теста, нажмите «Скопировать из»,
выберите источник и нажмите «ОК». Существующие параметры заменятся копией;
порядок, литералы, ссылки на глобальные переменные и переопределения сохраняются.
Копию можно сразу редактировать. Метаданные текущего теста и исходный тест
не меняются. Пустой источник не очищает конструктор — API возвращает ошибку.

`POST /api/testing/v1/test-definitions/{test_id}/args/copy-from`, тело
`{"source_test_id": "tdef_..."}`, возвращает новый список слотов. Операция требует
права `test_definition.update`, выполняется одной транзакцией и публикует
событие `test_command_arg.copy`. Миграция БД не нужна.

## Что НЕ делает сервис (пока)

- Не хранит платформенные mgmt-креды стендов — их держит `server_service` (§5.1 плана миграции).
- Не хранит токены Jira/Confluence/Zephyr — они в `secret_service` (department-scope credential, §2.4).
- Не дублирует ACS/Clonezilla-снимки, ASTRA/ALLTA health хоста, RC/build-каталог — всё это уже в `server_service` (§0 плана миграции).
