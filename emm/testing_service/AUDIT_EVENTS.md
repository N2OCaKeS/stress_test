# testing_service · audit events

Каталог событий, которые testing_service публикует в `loging_service` через `audit_service.emit(...)`. Источник истины — `src/services/audit_events.py` (`SERVICE_EVENTS` + `_DEFAULT_SEVERITY`); этот документ дублирует таблицу для удобства ревью.

## Общие принципы

- Имя действия — `<object>.<verb>`.
- Severity-дефолты задаются для пары `(action, status)` в `_DEFAULT_SEVERITY`. `loging_service` может переопределить/подавить их своими rule'ами.
- Поля envelope: `actor_id`, `actor_type` (`user` / `bot` / `oauth_client` / `service`), `username`, `department_id`, `target_id`, `target_type`, `service`, `request_id`, `actor_ip`, `user_agent`, `details`, `status`, `severity`, `timestamp`.
- `details.*` проходит через `redact_payload` (см. `src/services/redaction.py`) — пароли/токены/секреты маскируются плейсхолдерами.
- Health/ready НЕ логируются.

## Lifecycle

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `service.started` | success | INFO | Эмитится best-effort на регистрации каталога событий (не на каждый старт процесса — регистрация каталога, не сам факт запуска, см. `services/audit_events.py:register_events`). |

## HTTP middleware

`AuditAccessMiddleware` пишет outcome каждого HTTP-запроса, кроме `/health`/`/ready`.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `http.client_error` | failure | WARNING | 4xx ответ (кроме 401/403/501). `details = { method, path, status_code }`. |
| `http.access_denied` | denied | CRITICAL | 401 или 403. `details = { method, path, status_code }`. |
| `http.server_error` | failure | CRITICAL | 5xx ответ. `details = { method, path, status_code }`. |

## Global variables

Каталог платформенный, чтение не аудитится (как у `os_version`/`server_category` в `server_service`) — события пишутся только на запись.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `global_variable.create` | success | INFO | `details = { code, source, is_sensitive }`. |
| `global_variable.create` | denied | WARNING | Нет права `create`. `details = { reason: permission_denied }`. |
| `global_variable.create` | failure | WARNING | Дубль `code`. `details = { reason: duplicate, code }`. |
| `global_variable.update` | success | INFO | `details = { fields, code }`. |
| `global_variable.update` | denied | WARNING | Нет права `update`. |
| `global_variable.update` | failure | WARNING | `reason` = `not_found` либо `duplicate`. |
| `global_variable.delete` | success | WARNING | `details = { code }`. |
| `global_variable.delete` | denied | WARNING | Нет права `delete`. |
| `global_variable.delete` | failure | WARNING | `details = { reason: not_found }`. |

## Копирование параметров теста

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `test_command_arg.copy` | success | INFO | Параметры заменены копией из другого теста. `target_id` — текущий тест; `details = { source_test_id, count }`. |
| `test_command_arg.copy` | denied | WARNING | Нет права `test_definition.update`. `details = { source_test_id, reason }`. |
| `test_command_arg.copy` | failure | WARNING | Источник пуст, не найден, совпадает с текущим тестом или произошла ошибка записи. Транзакция отменена. `details = { source_test_id, reason }`. |

Значения параметров и секреты в событие не включаются.

## Домен (появится по волнам)

По `ALLTA MIGRATION.md` §11 ожидаются (пока не реализовано):

`test.launch`, `test.cancel`, `stp.status_updated`, `stp.test_case_created`, `test_definition.updated`, `department_report.generated`, `run_summary_comment.posted` и т.д. — заводятся вместе с сервисом, который их производит. Имена приводятся к конвенции `<object>.<verb>` (§11 плана пишет `global_variable.created` — в коде `global_variable.create`, как в остальных сервисах emm).

## Отказ допуска ожидающего теста

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `queue_item.launch_rejected` | denied | WARNING | `test_id`, актуальный `readiness`; обычный запуск остановлен из-за ручной смены статуса каталога до подготовки или выдачи воркеру. |

## Управление очередью стенда

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `queue_item.interrupt_requested` | success | WARNING | `stand_id`, `action` (`skip`/`pause`); заявка оператора на обрыв уже идущей SSH-сессии — сам обрыв делает `testing_worker`. |
| `queue_item.skipped` | success | WARNING | `stand_id`, `from_state`; тест снят с исполнения без исхода, стенд продолжает со следующего элемента. |
| `queue_item.paused` | success | WARNING | `stand_id`, `from_state`; тест снят с исполнения без исхода, стенд стоит до `resume-queue`. |
| `queue_item.resumed` | success | INFO | `stand_id`, новая `position`; остановленный элемент вернулся в конец очереди стенда. |
