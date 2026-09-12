# testing_service · реестр тестов

**Стек:** pytest + `httpx.AsyncClient(ASGITransport)` + реальный PostgreSQL + Redis через `tests/docker-compose.test.yml` (не SQLite/моки).

## Запуск

```bash
# В Docker (изолированные контейнеры)
make test-testing

# Один файл / тест
make test-dev TEST=testing_service/tests/test_health.py
```

## Реестр

| Файл | Кол-во (≈) | Что покрывает |
|---|---|---|
| `tests/test_health.py` | 5 | `/health` / `/ready` (реальные БД+Redis) / 404 на неизвестном пути / security headers / X-Request-ID echo. |
| `tests/test_global_variables_crud.py` | 34 | Сиды миграции (обязательный набор §2.1, `is_sensitive`, `choices_source`), CRUD, права (`admin` проходит, роль без грантов и `guest` — 403, аноним — 401), UNIQUE(code) → 409, валидация кода и `choices_source`, OpenAPI-схема. |
| `tests/test_choices_resolvers.py` | 23 | `static:` (массив строк / объектов / битый JSON), `dynamic:os_versions` и `dynamic:kernels` против замоканного `server_service` (httpx `MockTransport`), обязательный `os_version_id`, 503 на недоступном источнике, реестр `RESOLVERS`. |
| `tests/test_readiness.py` | 17 | Допуск всех четырёх статусов в обычном/debug-режиме, отклонение неизвестного/null, дефолт нового теста, смена статуса перед claim и подготовкой следующего задания, отсутствие retry при отказе допуска, независимость статуса от исхода запуска, частичный отказ кампании. |
| `tests/test_readiness_migration.py` | 1 | Upgrade существующего каталога со старыми, пустыми и неизвестными статусами; только прежний ready сохраняет обычный допуск. |
| `tests/test_campaign_attempts.py` | 5 | Итог после retry и сохранение старой попытки, неизменность состава при правках каталога, дедупликация стендов, учёт ошибок постановки, миграция независимых цепочек, смена привязки во время постановки. |
| `tests/test_public_queue.py` | 9 | Конкурентный идемпотентный запуск/retry, конфликт ключа, права отдела, допуск СТП, запрет лишних полей, debug-изоляция результатов, удаление теста из СТП до claim, фильтрация истории, сохранение кампании/стенда при ручном retry. |

## Что появится дальше

Unit/integration-сьюты для каждой волны из `obsidian/ALLTA MIGRATION.md` §16 (глобальные переменные, каталог тестов, стенды, SSH-worker + очередь, логи, прогоны, СТП/Zephyr/Confluence, changelog, department-отчёты) — по мере реализации соответствующего домена.

## Инфраструктура

`tests/conftest.py`:

- Дефолтные env'ы для `Settings` (`DATABASE_URL`, `REDIS_URL`, `AUTH_SERVICE_URL`, `APP_ENV=test`) — проставляются `tests/docker-compose.test.yml`, не мокаются.
- Фикстура `client` — ASGI-клиент поверх `src.main:app`.
- Реальный Postgres/Redis — своя изолированная пара контейнеров (`tests/docker-compose.test.yml`), не шарится с dev-стеком.
- Схема поднимается один раз на сессию: `DROP SCHEMA public CASCADE` + `alembic upgrade head` (сиды миграций должны быть на месте). После каждого теста удаляются строки `global_variables` с непустым `created_by` — то есть созданные через API, сиды остаются.
- `_patch_introspect` подменяет `src.dependencies.auth._introspect`; фабрика `make_token` + фикстуры `admin_token` / `guest_token` / `no_role_token` выдают токены с нужным набором сервисных ролей.
- Исходящие вызовы в `server_service` мокаются подменой `server_client.build_client` на `httpx.AsyncClient(transport=MockTransport(...))` — реальный `server_service` в тестах не поднимается.
