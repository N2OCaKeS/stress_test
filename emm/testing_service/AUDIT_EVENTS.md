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
| `global_variable.create` | failure | WARNING | Дубль `code` либо невалидное определение. `details = { reason: duplicate \| VARIABLE_SOURCE_REF_INVALID \| VARIABLE_TEMPLATE_UNKNOWN \| VARIABLE_TEMPLATE_CYCLE, code }`. |
| `global_variable.update` | success | INFO | `details = { fields, code }`. |
| `global_variable.update` | denied | WARNING | Нет права `update`. |
| `global_variable.update` | failure | WARNING | `reason` = `not_found`, `duplicate`, `VARIABLE_SOURCE_REF_INVALID`, `VARIABLE_TEMPLATE_UNKNOWN`, `VARIABLE_TEMPLATE_CYCLE` либо `GLOBAL_VARIABLE_IN_USE` (переименование кода, на который ссылаются шаблоны). |
| `global_variable.delete` | success | WARNING | `details = { code }`. |
| `global_variable.delete` | denied | WARNING | Нет права `delete`. |
| `global_variable.delete` | failure | WARNING | `details = { reason: not_found \| in_use }`; `in_use` — ссылка из слота, шаблона другой переменной или `override_value`. |

## Копирование параметров теста

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `test_command_arg.copy` | success | INFO | Параметры заменены копией из другого теста. `target_id` — текущий тест; `details = { source_test_id, count }`. |
| `test_command_arg.copy` | denied | WARNING | Нет права `test_definition.update`. `details = { source_test_id, reason }`. |
| `test_command_arg.copy` | failure | WARNING | Источник пуст, не найден, совпадает с текущим тестом или произошла ошибка записи. Транзакция отменена. `details = { source_test_id, reason }`. |

Значения параметров и секреты в событие не включаются.

## Шаги многоступенчатого теста

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `test_step.create` | success | INFO | Шаг добавлен. `target_id` — шаг; `details = { test_id, position, run_mode, copied_args }`. |
| `test_step.update` | success | INFO | `details = { test_id, fields }`. |
| `test_step.delete` | success | WARNING | Шаг удалён вместе со слотами. `details = { test_id }`. |
| `test_step.reorder` | success | INFO | `target_id` — тест; `details = { test_id, previous_order, new_order }`. |
| `test_step.*` | denied | WARNING | Нет права `test_definition.update`. `details = { test_id, reason }`. |
| `queue_item.step_completed` | success | INFO | Шаг item'а выполнен (код выхода 0), item переходит к следующему. `details = { stand_id, step_index, step_name, next_step_index, step_count }`. Вердикт — только после последнего шага. |
| `queue_item.stand_setup_requested` | success | INFO | Перед шагом запрошена настройка стенда без restore (CONTRACTS C2). `details = { stand_id, step_index, correlation_id, stand_setup_request_id, kernel_cmdline_extra }`; исход — `stand_setup.completed`, затем `queue_item.ready` (`details.step_index`) или `queue_item.prepare_failed`. |

Текст скрипта настройки в события не попадает.

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
| `queue_item.awaiting_verdict` | success | INFO | SSH-сессия закончилась с кодом 0, у теста `verdict_source=zephyr` и есть прогон в Zephyr — стенд держится, пока не прочитан статус. `exit_code`, `stand_id`, `zephyr_test_run_key`, `zephyr_test_case_key`. |
| `queue_item.verdict_resolved` | success | INFO | вердикт из Zephyr вынесен. `verdict` (`passed`/`failed`), `zephyr_status` (как есть), `timed_out` (статус не стал финальным за `zephyr_verdict_wait_seconds`, итог — `zephyr_verdict_unfinished_outcome`), `stand_id`. Следом — обычный `queue_item.completed` (success/failure). |
| `queue_item.skipped` | success | WARNING | `stand_id`, `from_state`; тест снят с исполнения без исхода, стенд продолжает со следующего элемента. |
| `queue_item.paused` | success | WARNING | `stand_id`, `from_state`; тест снят с исполнения без исхода, стенд стоит до `resume-queue`. |
| `queue_item.resumed` | success | INFO | `stand_id`, новая `position`; остановленный элемент вернулся в конец очереди стенда. |
| `queue_item.delete` | success | WARNING | `stand_id`, `from_state`; элемент убран из очереди насовсем — терминальной записи (как у `skip`) не остаётся. Недоступно для `preparing`/`ready`/`running` — сначала `skip`/`pause`. |
| `queue.force_takeover` | success | WARNING | `stand_id`, `server_id`, `queue_item_id`, `previous_holder` (из ответа server_service); админ отобрал бронь стенда у человека (`busy`/`testing_done`) через `force`; эмитится, только когда server_service вернул `previous_holder` (захват свободного стенда с `takeover=true` — обычный, без события). |
| `queue.force_takeover` | failure | WARNING | `stand_id`, `reason=state_not_takeable`, данные держателя; `force` не помог — стенд в `updating`/чужом `acs`. |
| `queue.force_takeover` | denied | WARNING | `stand_id`, `reason` (`force_requires_admin`/`replace_requires_admin`); `force`/`replace` от не-админа отдела стенда. |
| `queue.replace` | success | WARNING | `stand_id`, `new_queue_item_id`, `cleared_queued_count`, `interrupted` (текущие item'ы, снятые skip'ом); админ выбрал «очистить очередь и запустить сразу». Каждый снятый item отдельно эмитит `queue_item.skipped`, очистка — `test_stand.queue_cleared`. |
| `test_stand.queue_cleared` | success | WARNING | `stand_id`, `count`; массово убраны все ещё не стартовавшие (`queued`) item'ы очереди стенда. |
| `test_stand.queue_reordered` | success | INFO | `stand_id`, `count`, `previous_order`, `new_order` (id `queued`-элементов); оператор вручную переставил ещё не начатые элементы очереди стенда (`PATCH /test-stands/{id}/queue/order`, D15). Активный/остановленный элемент не переставляется. Повтор с тем же порядком событие не эмитит. |
| `test_stand.queue_retry_failed` | success | INFO | `stand_id`, `retried_count`, `skipped_count`; массовый retry всех ещё не перезапущенных `failed`-item'ов стенда. Каждый заведённый retry-item отдельно эмитит свой обычный `queue_item.enqueued`. |

## Статистика: пересчёт и справочник семейств (D18)

Пересчёт и настройки внешнего сервиса статистики; справочник семейств — платформенный, чтение не аудитится. Запись справочника — под тем же `(statistics_settings, *, update)`, что настройки и ручной пересчёт.

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `statistics_settings.update` | success | INFO | `enabled`, `base_url_set`. |
| `statistics_settings.update` | denied | WARNING | `reason=permission_denied`. |
| `statistics_recalc.triggered` | success | INFO | `department_id`, `triggered_by=manual`, `category` (если выбрано ровно одно семейство), `categories` (весь выбранный набор; `null` — полный пересчёт). |
| `statistics_recalc.triggered` | denied | WARNING | `reason=permission_denied`. |
| `statistics_recalc.completed` | success / failure | INFO / WARNING | `triggered_by`, `test_run_id`, `category`, `categories`, `error` (при нескольких семействах — `key: причина` через `; `). |
| `statistics_category.create` | success | INFO | `key`, `path`, `enabled`. |
| `statistics_category.create` | denied | WARNING | `reason=permission_denied`. |
| `statistics_category.create` | failure | WARNING | `reason=duplicate`, `key`. |
| `statistics_category.update` | success | INFO | `key`, `fields`. |
| `statistics_category.update` | denied / failure | WARNING | `reason` = `permission_denied` / `not_found`. |
| `statistics_category.delete` | success | WARNING | `key`. |
| `statistics_category.delete` | denied / failure | WARNING | `reason` = `permission_denied` / `not_found`. |

## Профили подготовки и настройка стенда

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `provisioning_profile.create` | success | WARNING | `department_id`, значения профиля. |
| `provisioning_profile.update` | success | WARNING | `fields`. |
| `provisioning_profile.*` | denied | WARNING | Нет `(provisioning_profile, update)` / чужой отдел / общий профиль без матрицы. |
| `scenario.create` / `scenario.update` | success | INFO | `code`, `department_id`, `stp_test_case_code`, `stand_ids`, `actions` (виды действий по порядку). |
| `scenario.create` / `scenario.update` | failure | INFO | `reason` — код ошибки валидации (`SCENARIO_*`) или `duplicate`. |
| `scenario.delete` | success | WARNING | `code`. |
| `scenario.*` | denied | WARNING | Нет `(test_definition, create/update/delete)` в отделе сценария. |
| `scenario.preview` | success | INFO | `os_version_id`, `kernel`, `actions` (число действий). Каждое `run_test` дополнительно пишет `test_definition.launch_preview`. |
| `scenario_run.started` | success | INFO | `scenario_id`, `stand_ids`, `debug`, `stp_test_run_id`, `test_run_id` (запуск по СТП / из кампании). |
| `scenario_run.waiting_for_stands` | success | INFO | `blocked_by` — занятые стенды; взятые брони отпущены. |
| `scenario_run.stands_acquired` | success | INFO | `stand_ids` — брони всех стендов взяты. |
| `scenario_run.stop_requested` | success | WARNING | `state` на момент запроса. |
| `scenario_run.succeeded` / `failed` / `stopped` | success / failure | INFO / WARNING | `verdict`, `error`, `released` — отпущенные стенды. |
| `stand_setup.completed` | success / failure | INFO / WARNING | Callback server_service о настройке стенда без restore: `stand_setup_request_id`, `failed_step`, `error`; `target_id` — `correlation_id` (`<queue_item_id>:step<N>:<nonce>`). Ждущий item переходит в `ready` или проваливается (`services/queue_steps.py`); без ждущего item'а — только это событие. |

## Профили запуска

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `launch_profile.create` | success | WARNING | `department_id` (null — общий), `version_id`, `is_default`. |
| `launch_profile.update` | success | WARNING | `fields ⊆ {name, is_default}`. |
| `launch_profile.version_created` | success | WARNING | Новая действующая версия: `version_id`, `version`. |
| `launch_profile.*` | denied | WARNING | Нет `(launch_profile, update)` / чужой отдел / общий профиль без матрицы (`reason: permission_denied`). |

## Маппинг статусов Zephyr

`PUT`/`DELETE /zephyr-status-mappings/{department_id}`.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `zephyr_status_mapping.update` | success | INFO | Набор отдела заменён. `details = { items: [{zephyr_status, outcome}] }`. |
| `zephyr_status_mapping.reset` | success | INFO | Строки отдела удалены, действует набор по умолчанию. `details = { deleted }`. |
| `zephyr_status_mapping.update` / `.reset` | denied | WARNING | Нет `(department_test_settings, update)` / чужой отдел (`reason: permission_denied`). |

## Тестовая учётка отдела

`PUT /department-test-account/{department_id}`. Значения (логин, пароль, ключи) в событие не попадают — только имена изменённых частей.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `department_test_account.update` | success | WARNING | Учётка заведена или изменена. `details = { credential_id, created, fields }`, `fields ⊆ {login, password, ssh_key, home_template}`. |
| `department_test_account.update` | denied | WARNING | Нет `(department_test_account, update)` / чужой отдел (`reason: permission_denied`) либо secret_service отказал в записи (`reason: CREDENTIAL_ACCESS_DENIED`). |
| `department_test_account.update` | failure | WARNING | secret_service недоступен или конфликт credential. `details = { reason, fields }`. |

## Публичный compat `/rest/api/*` (D17)

Запросы без авторизации со стендов. Отказ по подсети отдельного события не
имеет: 403 `LEGACY_COMPAT_SOURCE_FORBIDDEN` пишется middleware как
`http.access_denied` (IP источника — в `actor_ip`). Выбор отдела для URL
интеграций пишется всегда — чтобы ошибку сопоставления «IP → стенд → отдел»
было видно.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `legacy_compat.department_resolved` | success | INFO | `get-jira-url`/`get-confluence-url`: отдел выбран. `actor_type=anonymous`, `target_id`/`department_id` — отдел; `details = { route, source_ip, network, reason, stand_ids }`, `reason` = `stand` (найден стенд с этим IP) \| `default` (стенда нет — отдел по умолчанию) \| `ambiguous` (стенды с этим IP в разных отделах — отдел по умолчанию). |
| `legacy_compat.department_resolved` | failure | WARNING | Отдел не определён: стенда нет и отдел по умолчанию не выбран (`reason=no_default`), ответ 404 `LEGACY_COMPAT_DEPARTMENT_UNKNOWN`. |

Настройки (`/legacy-compat/networks`, `/legacy-compat/settings`), право `(legacy_compat, update)`:

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `compat_network.create` | success | WARNING | Подсеть открыта для анонимного доступа. `details = { cidr, enabled, description }`. |
| `compat_network.update` | success | WARNING | `details = { fields, before: {cidr, enabled}, cidr, enabled }`. |
| `compat_network.delete` | success | WARNING | `details = { cidr }`. |
| `compat_network.*` | denied | WARNING | `reason=permission_denied`. |
| `compat_network.*` | failure | WARNING | `reason` = `duplicate` (`cidr`) \| `not_found`. |
| `legacy_compat_settings.update` | success | INFO | `details = { default_department_id, previous }`. |
| `legacy_compat_settings.update` | denied | WARNING | `reason=permission_denied`. |

## Превью запуска теста

`POST /test-definitions/{id}/launch-preview`. Превью раскрывает учётные данные отдела в памяти сервиса (как claim) — событие фиксирует, кто и для какого стенда его собирал. Значения и содержимое файлов в событие не попадают.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `test_definition.launch_preview` | success | INFO | Превью собрано (в том числе с ошибками этапов). `target_id` — тест; `details = { stand_id, os_version_id, kernel, mode, debug, testenv, errors }`, `errors` — коды ошибок этапов сборки. |
