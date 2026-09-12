# testing_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/testing/v1`.
> **Статус реализации:** каркас + каталог глобальных переменных (§2.1/§3.3 плана). Остальной домен появится по волнам `obsidian/ALLTA MIGRATION.md` §16.
> **Аудит-события:** перечислены в `AUDIT_EVENTS.md`.

## Общие правила

- В production только HTTPS (TLS-guard middleware отбивает cleartext).
- User-facing эндпоинты (появятся с доменом) требуют Bearer JWT/PAT/bot-token, выдаваемый `auth_service`. Identity ресолвится через `/api/auth/v1/authorization/introspect` на каждый запрос.
- Internal-эндпоинты (появятся в волне 2/5 — callback от `server_service` для `prepare-for-test`) будут закрыты shared bearer `SERVICE_API_KEY`/`SERVICE_API_KEYS`, `include_in_schema=False`.
- Health/ready публичны (без auth, без rate-limit).
- Все значимые действия публикуются в `loging_service` — каталог см. `AUDIT_EVENTS.md`.

## Health

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/health` | public | Liveness — без проверок зависимостей. |
| `GET` | `/ready` | public | Readiness — БД обязательна, Redis best-effort. |

## Global variables

Платформенный каталог переменных конструктора команд (§2.1, §3.3). Не per-department: один набор на всю платформу. Чтение — любой аутентифицированный актор (доступ отдела к сервису не проверяется, как у `os-versions`/`server-categories` в `server_service`); запись — матрица прав `(global_variable, <role>, create|update|delete)`, засеяна системной роли `admin`.

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/global-variables` | authenticated | Страница каталога, envelope `{items, total, limit, offset}`. |
| `GET` | `/global-variables/by-code/{code}` | authenticated | Карточка по UNIQUE-коду. |
| `GET` | `/global-variables/{id}` | authenticated | Карточка по id. |
| `GET` | `/global-variables/{id}/choices` | authenticated | Резолв `choices_source` в список `{value, label}` в момент запроса. |
| `POST` | `/global-variables` | `global_variable:create` | Завести переменную. UNIQUE(code) → 409. |
| `PATCH` | `/global-variables/{id}` | `global_variable:update` | Частичное обновление. |
| `DELETE` | `/global-variables/{id}` | `global_variable:delete` | Hard-delete. |

**`choices_source`** — способ получить список значений, а не сам список:

| Формат | Пример | Параметры | Что делает |
|---|---|---|---|
| `static:<json>` | `static:["orel","smolensk"]` | — | Фиксированное множество. JSON разбирается при резолве, не при сохранении. Допустим массив скаляров либо массив `{"value","label"}`. |
| `dynamic:os_versions` | — | — | Живой каталог OS-версий из `server_service`. |
| `dynamic:kernels` | — | `os_version_id` (обязателен) | Ядра конкретной версии ОС из карточки `server_service`. |

Пустой `choices_source` → значение свободного ввода, `/choices` отвечает 422 `CHOICES_SOURCE_NOT_SET`. Резолверы регистрируются в `src/services/choices.py` (`RESOLVERS`); имя, которого там нет, отбивается ещё при сохранении переменной.

**Error codes:** `GLOBAL_VARIABLE_NOT_FOUND` (404), `GLOBAL_VARIABLE_DUPLICATE` (409), `PERMISSION_DENIED` (403), `CHOICES_SOURCE_INVALID` / `CHOICES_SOURCE_NOT_SET` / `CHOICES_RESOLVER_UNKNOWN` (422), `CHOICES_PARAM_REQUIRED` (400), `SERVER_SERVICE_NOT_CONFIGURED` / `SERVER_SERVICE_UNREACHABLE` / `SERVER_SERVICE_TIMEOUT` / `SERVER_SERVICE_ERROR` (503), `SERVER_SERVICE_OBJECT_NOT_FOUND` (404).

## Формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "SERVICE_ACCESS_DENIED",
  "message": "...",
  "details": {},
  "request_id": "req_123",
  "timestamp": "2026-09-09T12:00:00Z"
}
```

## Домен (появится по волнам)

Легаси-совместимые пути (`GET /rest/api/get-repo-path-as-json` и т.д., §12 плана миграции) и весь остальной каталог (глобальные переменные, тесты, стенды, очередь, логи, СТП, отчёты) — по мере реализации соответствующей волны.

## Статус каталога тестов

`POST /test-definitions` и `PATCH /test-definitions/{id}` принимают `readiness`:

| Значение | Название | Допуск |
|---|---|---|
| `ready` | Рабочий | Обычный запуск и debug |
| `review` | На проверке | Только debug |
| `broken` | Неисправен | Только debug |
| `development` | В разработке | Только debug |

Новый тест без поля `readiness` создаётся в `development`. Явный `null` и неизвестные значения возвращают 422. PATCH без этого поля сохраняет статус. Исход попытки не меняет статус каталога.

Обычная постановка нерабочего теста отклоняется с `TEST_REQUIRES_DEBUG`; создание кампании возвращает эту причину в `enqueue_errors`. Повторная проверка выполняется перед подготовкой стенда и выдачей задания воркеру. Если статус изменён во время ожидания, попытка завершается с `state=failed`, `failed_step=launch_guard`, без исполнения, автоматического retry и записи результата в СТП. Запущенную попытку смена статуса не прерывает.

## Состав прогона и попытки

`POST /test-runs` удаляет повторяющиеся ID стендов и до постановки заданий сохраняет состав в `test_run_entries`: тест, стенд, код и имя на момент создания. Смена каталога после фиксации не меняет этот список. Ошибки постановки сохраняются в составе и повторно доступны при чтении карточки.

`GET /test-runs/{id}` дополнительно возвращает:

- `composition_source`: `pinned_catalog` для новых кампаний, `legacy_queue` для состава, восстановленного из существующей очереди. Это ещё не снимок ревизии СТП.
- `entries`: сохранённый состав и `enqueue_error_code`/`enqueue_error` для незапущенных элементов.
- `queue_items[].test_run_entry_id`, `retry_of_id`, `is_current`: принадлежность попытки элементу состава, предыдущая попытка и признак актуального результата. История из ответа не удаляется.
- `progress`: `total` логических тестов, `attempts` всех попыток и счётчики текущих состояний. Для состояния без элементов ключ может отсутствовать.

Итог кампании учитывает последнюю попытку цепочки. Падение → ожидающий retry означает running; падение → успешный retry означает succeeded, если остальные элементы тоже успешны. Независимые постановки одного теста не объединяются. Элемент с ошибкой постановки учитывается как failed, а ещё не поставленный элемент — queued.

Лог каждой попытки по-прежнему доступен через `/queue-items/{id}/log` и download/WS. Новый запуск не удаляет предыдущий лог. Периодическая очистка по сроку хранения и защита логов актуальных РЦ сохраняются.
