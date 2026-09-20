# testing_service · audit events

Каталог событий, которые testing_service публикует в `loging_service` через `audit_service.emit(...)`. Источник истины — `src/services/audit_events.py` (`SERVICE_EVENTS` + `_DEFAULT_SEVERITY`); этот документ дублирует таблицу для удобства ревью.

## Общие принципы

- Имя действия — `<object>.<verb>`.
- Severity-дефолты задаются для пары `(action, status)` в `_DEFAULT_SEVERITY`. `loging_service` может переопределить/подавить их своими rule'ами.
- Поля envelope: `actor_id`, `actor_type` (`user` / `bot` / `oauth_client` / `service`), `username`, `department_id`, `target_id`, `target_type`, `service`, `request_id`, `actor_ip`, `user_agent`, `details`, `status`, `severity`, `timestamp`.
- `details.*` проходит через `redact_payload` (см. `src/services/redaction.py`) — пароли/токены/секреты маскируются плейсхолдерами.
- Health/ready НЕ логируются.

## Доставка (durable outbox)

`emit()` не ходит в сеть. Он собирает payload и складывает его в буфер запроса, который outermost-middleware (`src/middleware/audit_outbox_scope.py`) по завершении запроса пишет одним INSERT'ом в таблицу `audit_outbox`. Доставку в `loging_service` делает фоновый цикл `services/audit_outbox_publisher.run_drain_loop` (поднимается в lifespan'е `main.py` рядом с ротацией логов).

- Гарантия — **at-least-once**: недоступный `loging_service` больше не означает потерянное событие, строка лежит в очереди и переотправляется. Дубликаты возможны (падение процесса между POST'ом и отметкой) и разруливаются на стороне `loging_service`.
- Выборка — `SELECT ... LIMIT 1 FOR UPDATE SKIP LOCKED` по `(created_at, id)`, по одной строке за итерацию с отдельным commit'ом.
- Ретраи — per-row backoff `next_retry_at = now() + 2^attempts` (cap 300 s), `Retry-After` с 429 уважается как нижняя граница.
- DLQ — `published_at` проставлен, `last_error` начинается с `[DLQ:<reason>]`, `reason ∈ {attempts_cap, permanent_4xx, missing_action}`; в лог уходит ERROR `audit_outbox: row sent to DLQ ...`, счётчик виден в `/ready` как `audit_outbox_dlq_total`.
- Доставленные и DLQ-строки чистятся по `AUDIT_OUTBOX_RETENTION_HOURS`, недоставленные не трогаются никогда.
- Если `LOGGING_SERVICE_URL`/`LOGGING_SERVICE_API_KEY` не заданы (dev/test), в outbox ничего не пишется и цикл не поднимается — событие остаётся в логе процесса (`audit_event ...`).
- Событие, эмитнутое вне запроса (фоновые циклы, `statistics_recalc`), пишется отдельной task'ой со своей сессией; счётчик не доехавших до таблицы виден в `/ready` как `audit_dropped_429_total` (имя ключа историческое).

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

## Креды учётки исполнения теста

`GET /test-stands/{id}/test-credentials` — прокси на `server_service`. Гейт один (`(test_stand, view_test_credentials)`), но аудит разведён по факту раскрытия, как `server.test_credentials_viewed` / `server.test_credentials_revealed` в `server_service`: SIEM должен отличать просмотр карточки от реального выноса секрета.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `test_stand.test_credentials_viewed` | success | WARNING | `reveal=false` — отданы только метаданные (`username`/`ssh_public_key`/`rotated_at`), секрета в ответе нет. `details = { reveal: false, server_id }`. |
| `test_stand.test_credentials_viewed` | denied | WARNING | Нет права `view_test_credentials`, запрос был без `reveal`. `details = { reveal: false, reason: permission_denied }`. |
| `test_stand.test_credentials_revealed` | success | CRITICAL | `reveal=true` — пароль и приватный ключ учётки уехали вызывающему. `details = { reveal: true, server_id }`. |
| `test_stand.test_credentials_revealed` | denied | WARNING | Попытка раскрытия отбита матрицей — секрет не раскрыт, поэтому WARNING, а не CRITICAL. `details = { reveal: true, reason: permission_denied }`. |

Отличие от `server_service`: там `viewed` — INFO, здесь оставлен WARNING. Просмотр карточки в testing_service доступен только держателю отдельного `view_test_credentials`, это не рядовая операция.

## Домен (появится по волнам)

По `ALLTA MIGRATION.md` §11 ожидаются (пока не реализовано):

`test.launch`, `stp.status_updated`, `stp.test_case_created`, `test_definition.updated`, `department_report.generated`, `run_summary_comment.posted` и т.д. — заводятся вместе с сервисом, который их производит. Имена приводятся к конвенции `<object>.<verb>` (§11 плана пишет `global_variable.created` — в коде `global_variable.create`, как в остальных сервисах emm).

`test.cancel` из этого списка реализован под именем `queue_item.delete` — см. «Управление очередью стенда» ниже; название переиграно в пользу конвенции `<object>.<verb>` этого сервиса, где объектом лифецикла очереди всегда выступает `queue_item`/`test_stand`, а не `test`.

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
| `queue_item.delete` | success | WARNING | `stand_id`, `from_state`; элемент убран из очереди насовсем — терминальной записи (как у `skip`) не остаётся. Недоступно для `preparing`/`ready`/`running` — сначала `skip`/`pause`. |
| `queue.force_takeover` | success | WARNING | `stand_id`, `server_id`, `queue_item_id`, `previous_holder` (из ответа server_service); админ отобрал бронь стенда у человека (`busy`/`testing_done`) через `force`. |
| `queue.force_takeover` | failure | WARNING | `stand_id`, `reason=state_not_takeable`, данные держателя; `force` не помог — стенд в `updating`/чужом `acs`. |
| `queue.force_takeover` | denied | WARNING | `stand_id`, `reason` (`force_requires_admin`/`replace_requires_admin`); `force`/`replace` от не-админа отдела стенда. |
| `queue.replace` | success | WARNING | `stand_id`, `new_queue_item_id`, `cleared_queued_count`, `interrupted` (текущие item'ы, снятые skip'ом); админ выбрал «очистить очередь и запустить сразу». Каждый снятый item отдельно эмитит `queue_item.skipped`, очистка — `test_stand.queue_cleared`. |
| `test_stand.queue_cleared` | success | WARNING | `stand_id`, `count`; массово убраны все ещё не стартовавшие (`queued`) item'ы очереди стенда. |
| `test_stand.queue_retry_failed` | success | INFO | `stand_id`, `retried_count`, `skipped_count`; массовый retry всех ещё не перезапущенных `failed`-item'ов стенда. Каждый заведённый retry-item отдельно эмитит свой обычный `queue_item.enqueued`. |
