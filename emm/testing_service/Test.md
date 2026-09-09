# testing_service · реестр тестов

**Стек:** pytest + `httpx.AsyncClient(ASGITransport)` + реальный PostgreSQL + Redis через `tests/docker-compose.test.yml` (не SQLite/моки).

## Запуск

```bash
# В Docker (изолированные контейнеры)
make test-testing

# Один файл / тест
make test-dev TEST=testing_service/tests/test_health.py
```

## Smoke (волна 1)

| Файл | Кол-во (≈) | Что покрывает |
|---|---|---|
| `tests/test_health.py` | 5 | `/health` / `/ready` (реальные БД+Redis) / 404 на неизвестном пути / security headers / X-Request-ID echo. |

## Что появится дальше

Unit/integration-сьюты для каждой волны из `obsidian/ALLTA MIGRATION.md` §16 (глобальные переменные, каталог тестов, стенды, SSH-worker + очередь, логи, прогоны, СТП/Zephyr/Confluence, changelog, department-отчёты) — по мере реализации соответствующего домена.

## Инфраструктура

`tests/conftest.py`:

- Дефолтные env'ы для `Settings` (`DATABASE_URL`, `REDIS_URL`, `AUTH_SERVICE_URL`, `APP_ENV=test`) — проставляются `tests/docker-compose.test.yml`, не мокаются.
- Фикстура `client` — ASGI-клиент поверх `src.main:app`.
- Реальный Postgres/Redis — своя изолированная пара контейнеров (`tests/docker-compose.test.yml`), не шарится с dev-стеком.
