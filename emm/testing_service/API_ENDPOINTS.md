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
| `GET` | `/global-variables/source-options` | authenticated | Допустимые значения `source_ref` по источникам: `sources`, `test_fields`, `stand_fields`, `stand_ref_fields`, `department_integration_fields[{field, is_credential}]`, `credential_parts`, `os_version_fields`, `test_account_fields`, `zephyr_folder_fields`, `template_conditions`. Те же множества проверяются при сохранении. |
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

**`source` + `source_ref`** (формат — `emm/tasks/testing_parity/CONTRACTS.md` C1; резолв — `src/services/variable_resolver.py`):

| `source` | `source_ref` | Значение |
|---|---|---|
| `launch_context` / `per_test_override` / `secret_service` | `null` | `launch_context[code]` (или `override_value` слота) |
| `static` | `{"value": "..."}` или `null` | фиксированное значение; `null` — как `launch_context` |
| `template` | `{"template": "text {CODE}", "when"?: "debug" \| "not_debug"}` | подстановка других переменных, рекурсивно; вне `when` — пустая строка |
| `test_field` | `{"field": "short_name" \| "full_name" \| "changelog_component" \| "category" \| "code", "fallback"?: <поле>}` | поле теста; пустое → `fallback` |
| `stand` | `{"field": "legacy_token" \| "number" \| "host" \| "id", "fallback"?: <поле>}` | поле стенда; `number` — цифры `legacy_token`, `host` — connection-info |
| `department_integration` | `{"field": <колонка department_integration_settings>, "fallback"?: <колонка того же вида>, "credential_part": null \| "login" \| "secret"}` | настройки интеграций отдела стенда; пустое поле → `fallback`; `*_credential_id` — reveal в secret_service (`secret` требует `is_sensitive=true`) |
| `os_version` | `{"field": "name" \| "rc_number" \| "is_urgent_update", "segments": N \| null, "uu_segments": N \| null}` | резолв — до появления поддержки `name` без сегментов = `launch_context["RC"]` |
| `test_account` | `{"field": "login" \| "password" \| "home"}` | резолв; до него 422 `VARIABLE_SOURCE_NOT_CONFIGURED` |

При сохранении: `source_ref` проверяется под `source` (422 `VARIABLE_SOURCE_REF_INVALID`), шаблон ссылается только на существующие коды (422 `VARIABLE_TEMPLATE_UNKNOWN`) и не образует цикла (422 `VARIABLE_TEMPLATE_CYCLE`). Удалить или переименовать переменную, чей `{CODE}` упомянут в шаблоне другой переменной или в `override_value` слота, нельзя — 409 `GLOBAL_VARIABLE_IN_USE` (`details.referenced_by_variables`, `details.referenced_by_tests`). `override_value` слота (`/test-definitions/{id}/args`) тоже понимает `{CODE}`; неизвестный код → 422 `VARIABLE_TEMPLATE_UNKNOWN`.

Маскировка: токен команды, в который попала хоть одна `is_sensitive`-переменная (напрямую, через шаблон или `override_value`), в `*_masked` отдаётся как `***` целиком.

Ошибки резолва на claim (попадают в `queue_items.error` как `resolve_dates_content failed: …`): `LAUNCH_CONTEXT_VARIABLE_MISSING`, `VARIABLE_VALUE_MISSING`, `VARIABLE_CONTEXT_MISSING`, `VARIABLE_SOURCE_NOT_CONFIGURED`, `DEPARTMENT_INTEGRATION_NOT_CONFIGURED`, `VARIABLE_TEMPLATE_UNKNOWN`, `VARIABLE_TEMPLATE_CYCLE`, плюс ошибки `secret_service`/`server_service`.

**Error codes:** `GLOBAL_VARIABLE_NOT_FOUND` (404), `GLOBAL_VARIABLE_DUPLICATE` / `GLOBAL_VARIABLE_IN_USE` (409), `PERMISSION_DENIED` (403), `VARIABLE_SOURCE_REF_INVALID` / `VARIABLE_TEMPLATE_UNKNOWN` / `VARIABLE_TEMPLATE_CYCLE` (422), `CHOICES_SOURCE_INVALID` / `CHOICES_SOURCE_NOT_SET` / `CHOICES_RESOLVER_UNKNOWN` (422), `CHOICES_PARAM_REQUIRED` (400), `SERVER_SERVICE_NOT_CONFIGURED` / `SERVER_SERVICE_UNREACHABLE` / `SERVER_SERVICE_TIMEOUT` / `SERVER_SERVICE_ERROR` (503), `SERVER_SERVICE_OBJECT_NOT_FOUND` (404).

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

## Короткое имя и экранирование dates.conf

`POST /test-definitions`, `PATCH /test-definitions/{id}` и ответы карточки теста несут два поля:

| Поле | Значения | Смысл |
|---|---|---|
| `short_name` | строка до 64 символов или `null` | короткое имя теста (`XFS`, `postgresql-sm`) — легаси-ключ словаря `tests` (`allta_image_conf.py:237-305`). Его подставляет переменная `TEST_SHORT_NAME` (в `--confluence-new-page`); пусто — полное имя. |
| `dates_quoting` | `shell` (по умолчанию) \| `legacy` \| `raw` | как токены команды склеиваются в `dates.conf`: `shell` — `shlex.quote` каждого токена (после `shlex.split` получается ровно список токенов); `legacy` — в `"…"` берутся токены с пробелом, как `backup_image.py`; `raw` — через пробел без экранирования. Маскированная версия (`dates_content_masked`) экранируется так же. Явный `null` в PATCH — 422. |

## Превью запуска

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/test-definitions/{id}/launch-preview` | чтение теста и стенда (свой отдел) | Задание воркеру (CONTRACTS C3), которое собрал бы claim, без постановки в очередь и без записи в БД. `step_index` (с 0) — задание для этого шага многоступенчатого теста: слоты, суффикс и настройка стенда шага, `rerun` — `rerun_script` без git-токена; в ответе `step {index, count, name, run_mode}`. |

Тело: `{stand_id, os_version_id, kernel, mode?, debug?, testenv?}` — `mode`
по умолчанию режим теста; `testenv=true` — одиночный запуск «только
подготовка» (маркер `on`, на стенд уходит ещё файл команды).

Ответ: `launch_context` (`RC`/`KERNEL`/`MODE`), `launch_profile`
(`profile_id`, `name`, `version_id`, `version`), `variables[]`
(`code`, `label`, `source`, `value`, `sensitive`; `source=claim` — значения
задания: пути профиля, `QUEUE_ITEM_ID`, аргументы clone),
`dates_content_masked`, `files[]` (`role` — ключ пути профиля: `script`,
`token`, `dates`, `testenv_marker`, `command_file`; `path`, `mode`,
`sensitive`, `content`), `launch_command_masked`, `stop_command`, `use_pty`,
`cleanup_globs`, `errors[]` (`stage`: `launch_profile` | `paths` | `dates` |
`git_token` | `launch`; `error_code`, `message`, `details`).

Сборка — те же функции, что у claim (`launch_profile.render_paths`/
`build_launch`, `test_command_arg.resolve_dates`, `queue.resolve_git_token`),
в одном контексте резолва. Отличия: `{QUEUE_ITEM_ID}` = `qi_preview`;
этап, который не удался, не обрывает превью (в `errors` тот же код, с
которым упал бы claim); секреты — `***`: значения sensitive-переменных,
содержимое файлов по `redact_values` задания (токен целиком), `dates.conf`
— маскированная строка. Аудит — `test_definition.launch_preview`.

**Error codes:** `TEST_DEFINITION_NOT_FOUND` / `TEST_STAND_NOT_FOUND` (404),
`DEPARTMENT_ISOLATION` / `PERMISSION_DENIED` (403, тест другого отдела, чем
стенд).

## Состав прогона и попытки

`POST /test-runs` удаляет повторяющиеся ID стендов и до постановки заданий сохраняет состав в `test_run_entries`: тест, стенд, код и имя на момент создания. Смена каталога после фиксации не меняет этот список. Ошибки постановки сохраняются в составе и повторно доступны при чтении карточки.

`GET /test-runs/{id}` дополнительно возвращает:

- `composition_source`: `pinned_catalog` для новых кампаний, `legacy_queue` для состава, восстановленного из существующей очереди. Это ещё не снимок ревизии СТП.
- `entries`: сохранённый состав и `enqueue_error_code`/`enqueue_error` для незапущенных элементов.
- `queue_items[].test_run_entry_id`, `retry_of_id`, `is_current`: принадлежность попытки элементу состава, предыдущая попытка и признак актуального результата. История из ответа не удаляется.
- `progress`: `total` логических тестов, `attempts` всех попыток и счётчики текущих состояний. Для состояния без элементов ключ может отсутствовать.

**Порядок постановки (D15).** Состав кампании (оба пути сборки — явный пул и вывод из активной СТП) ставится в очереди стендов в порядке правила отдела `campaign_sort_rule` из `GET/PUT /department-test-settings/{department_id}`: список `{"key", "direction"}`, ключи `mode`, `kernel`, `test_case_name` (имя тест-кейса СТП, иначе название теста), `test_code`, `priority` (`test_definitions.priority`), направление `asc`/`desc`, 1–5 ключей без повторов. По умолчанию — легаси: режим → ядро → имя тест-кейса, всё по возрастанию строкой. Равные по правилу записи упорядочены по стенду, коду теста, ядру и режиму — порядок детерминирован. `POST /test-runs/preview` показывает записи в том же порядке. Одиночные и debug-запуски через `POST /queue-items`, retry и `resume-queue` правилу не подчиняются — встают в конец очереди (FIFO). В аудите `test_run.create` — применённое правило (`sort_rule`).

Итог кампании учитывает последнюю попытку цепочки. Падение → ожидающий retry означает running; падение → успешный retry означает succeeded, если остальные элементы тоже успешны. Независимые постановки одного теста не объединяются. Элемент с ошибкой постановки учитывается как failed, а ещё не поставленный элемент — queued.

Лог каждой попытки по-прежнему доступен через `/queue-items/{id}/log` и download/WS. Новый запуск не удаляет предыдущий лог. Периодическая очистка по сроку хранения и защита логов актуальных РЦ сохраняются.

## Публичные одиночные запуски и ручной retry

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/queue-items` | Один тест вне кампании. |
| POST | `/queue-items/{id}/retry` | Повтор последней терминальной попытки в её исходном контексте. |
| GET | `/queue-items` | Очередь/история своего отдела: `kind=standalone` (по умолчанию), `campaign` или `all`, optional `test_run_id`, `limit`/`offset`. |

POST запуска: обязательные `request_id` (8–128 символов), `test_id`, `stand_id`, `os_version_id`, `kernel`, `mode` (`orel`/`smolensk`), optional `debug_mode` (false по умолчанию), `force` (false), `on_active_queue` (`append`/`replace`, по умолчанию не задан) — см. «Занятый стенд, force и режимы очереди» ниже. POST retry: `request_id` и optional `force`; стенд, тест, контекст и кампания наследуются, на активной очереди retry всегда встаёт в конец (`append`), режим не принимается. Произвольные поля, включая `test_run_id` и секреты в launch_context, не принимаются.

Запись разрешена администратору своего отдела или роли с `test_run:create` в своём отделе. Чужой стенд, тест чужого отдела и чужая кампания отклоняются. Чтение списка ограничено стендами своего отдела.

Повторная отправка с тем же actor/request_id и теми же параметрами возвращает тот же queue_item (201). При других параметрах — 409 `REQUEST_ID_CONFLICT`. Две конкурентные отправки сериализуются; request_id и fingerprint записываются одной транзакцией с попыткой. Попытка, которая ещё активна или уже имеет продолжение, не допускает новый retry (409 `RETRY_NOT_ALLOWED`). Результат ранее принятого запроса можно повторно получить тем же request_id.

Обычный одиночный запуск требует закреплённого стенда, статуса «Рабочий» и ячейки теста в СТП соответствующего стенда/РЦ/ядра/режима. Ручной обычный retry сохраняет исходный стенд, даже если привязка изменилась. Оба пути проверяют СТП; `TEST_NOT_IN_STP` — 422. Выбранный `stp_test_run_id` сохраняется в очереди, наследуется автоматическим retry и повторно проверяется перед подготовкой/выдачей воркеру.

Debug допускается вне СТП и не меняет локальные ячейки или результаты Zephyr. Обычная попытка с сохранённой ссылкой обновляет выбранную СТП; legacy-попытки без ссылки сохраняют старое сопоставление по контексту. Старое создание кампании пока не требует СТП для каждого элемента — унификация этого входа относится к E1 и D4.

Ответ содержит ID попытки/теста/стенда, test_run_id/retry_of_id, debug_mode, state, `interrupt_action`, RC/kernel/mode и времена/ошибку. Полный launch_context, ключ стэша и креды в ответ не входят.

### Занятый стенд, force и режимы очереди

Общий контракт для `POST /queue-items`, `POST /queue-items/{id}/retry`, `POST /test-stands/{id}/retry-failed` и `POST /test-runs`. Постановка смотрит на очередь testing_service на стенде и, если она пуста, на бронь стенда в server_service (`POST /internal/servers/batch-status`).

Поля запроса:

- `force` (bool, по умолчанию `false`) — запустить, даже если стенд занят не нами. Право проверяет сервер: `department_admin` отдела стенда либо роль `admin` testing_service в этом отделе. Не-админ с `force=true` на занятом стенде получает 403 `FORCE_LAUNCH_DENIED` (а не тихий запуск без force). На свободном стенде и для не-админа на пустой очереди `force` ничего не меняет.
- `on_active_queue` (`append` | `replace`, по умолчанию не задан) — что делать, если очередь стенда уже активна (идёт тест или есть `queued`). Есть у `POST /queue-items` и `POST /test-runs`; у `retry` и `retry-failed` режима нет — они всегда `append`.

Матрица результата (очередь пуста):

| Стенд | Без `force` | С `force`, админ отдела стенда | С `force`, не-админ |
|---|---|---|---|
| `free` | запуск | запуск | запуск |
| `busy` (человек), `testing_done` | 409 `STAND_BUSY` | бронь отбирается у держателя, запуск | 403 `FORCE_LAUNCH_DENIED` |
| `updating` (обновление ОС), `acs` (чужое восстановление образа), прочее | 409 `STAND_BUSY` | 409 `STAND_TAKEOVER_NOT_ALLOWED` | 403 `FORCE_LAUNCH_DENIED` |

Матрица результата (очередь стенда уже активна; бронь наша, `force` не нужен):

| Вызывающий | `on_active_queue` не задан | `append` | `replace` |
|---|---|---|---|
| не-админ | молча в конец очереди | в конец | 403 `FORCE_LAUNCH_DENIED` |
| админ отдела стенда | 409 `STAND_QUEUE_ACTIVE` | в конец | очистить остальные `queued`, прервать текущий тест (`skipped`), сразу начать новый |

`replace` порядком: новый item заводится, затем удаляются прочие `queued` (кроме только что заведённых этим запросом), затем текущий `running`/`preparing`/`ready`/`paused` снимается через skip. Бронь стенда при этом не отпускается — очередь продолжает именно новый item. Если очередь опустела между проверкой и блокировкой стенда (последний item завершился, стенд ушёл в `testing_done`), админ с `replace` или `force` получает захват с отбором брони, а не 409 `SERVER_ALREADY_BUSY`.

Коды ошибок:

| Код | HTTP | Когда | `details` |
|---|---|---|---|
| `STAND_BUSY` | 409 | Стенд занят не нами, `force` не передан | `busy_state`, `busy_actor_type` (`user`/`service`), `busy_service_name`, `busy_user_id`, `busy_user_name`, `busy_note`, `takeover_possible` (bool: `true` для `busy`/`testing_done` — админ может повторить с `force=true`; `false` для `updating`/`acs`). Поля держателя берутся из `batch-status` server_service; `busy_user_name` может быть `null` — UI резолвит `busy_user_id` через auth_service. |
| `STAND_TAKEOVER_NOT_ALLOWED` | 409 | `force=true`, но стенд в `updating` либо чужом `acs` — отобрать нельзя | те же поля держателя, что у `STAND_BUSY` |
| `STAND_QUEUE_ACTIVE` | 409 | Админ поставил тест в стенд с активной очередью без `on_active_queue` | `stand_id`, `queued_count`, `current` (`queue_item_id`, `state`, `test_id`, `test_code`, `test_name`, `started_at`, `test_run_id`; `null`, если текущего нет) |
| `FORCE_LAUNCH_DENIED` | 403 | `force=true` на занятом стенде или `on_active_queue=replace` от не-админа отдела стенда | — |

Каждый отказ по `force`/`replace` пишет audit `queue.force_takeover` (`denied`/`failure`), успешный отбор брони — `queue.force_takeover` (`success`, с `previous_holder`), успешная замена очереди — `queue.replace` (см. `AUDIT_EVENTS.md`).

Различия по эндпоинтам:

- `POST /queue-items` — ошибки отдаются как обычный HTTP-ответ с кодом и `details` выше; повтор тем же `request_id` с изменёнными `force`/`on_active_queue` — 409 `REQUEST_ID_CONFLICT` (поля входят в отпечаток запроса).
- `POST /queue-items/{id}/retry` — `force` как выше. Право админа перепроверяется сервером, `on_active_queue` не принимается (`extra=forbid`).
- `POST /test-stands/{id}/retry-failed` — необязательное тело `{"force": bool}` (без тела = `false`), применяется к каждому перезапускаемому item'у. Занятый стенд без `force` — 409 `STAND_BUSY` на весь вызов (item'ы, заведённые до него, остаются); на активной очереди у админа вопроса нет, item'ы встают в конец.
- `POST /test-runs` — `force` и `on_active_queue` общие для всех стендов кампании. Провал одного стенда не рушит кампанию: `STAND_BUSY`, `STAND_QUEUE_ACTIVE`, `STAND_TAKEOVER_NOT_ALLOWED`, `FORCE_LAUNCH_DENIED` попадают в `enqueue_errors[]` (`stand_id`, `test_id`, `error_code`, `message`, `details`), ответ остаётся 201. `details` (держатель стенда, состав очереди) отдаются только в ответе на создание, при повторе по `request_id` не восстанавливаются — в `entries[].enqueue_error_code`/`enqueue_error` остаются код и сообщение. Режим `on_active_queue` относится к первому тесту стенда в кампании, остальные тесты этого стенда встают в конец (иначе `replace` стирал бы уже заведённое). Оба поля входят в отпечаток идемпотентности.
- `POST /test-runs/preview` — принимает то же тело, но `force`/`on_active_queue` игнорирует и не смотрит на занятость стендов: показывает только состав и причины пропуска. Занятость выясняется при реальном запуске.

### Управление очередью стенда

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/queue-items/{id}/skip` | Снять попытку с исполнения без исхода — стенд продолжает со следующей. |
| POST | `/queue-items/{id}/pause` | Снять попытку с исполнения и остановить стенд до явного продолжения. |
| POST | `/test-stands/{id}/resume-queue` | Вернуть остановленную попытку в конец очереди стенда и продолжить её. |
| PATCH | `/test-stands/{id}/queue/order` | Переставить ещё не начатые (`queued`) попытки стенда. |

У skip/pause/resume-queue тела запроса нет, ответ всех трёх — та же карточка попытки (`PublicQueueItem`), допуск — как у постановки в очередь (`test_run:create` в отделе стенда либо администратор отдела). `PublicQueueItem.position` — место в очереди стенда (меньше — раньше).

`PATCH /test-stands/{id}/queue/order` (D15) — тело `{"queue_item_ids": [...]}`: все текущие `queued`-попытки стенда в новом порядке; ответ `{"items": [PublicQueueItem...]}` в новом порядке. Допуск тот же. Попытки получают те же значения `position`, что уже занимали, так что блок `queued` относительно остальной очереди не сдвигается. Активная (`preparing`/`ready`/`running`) и остановленная (`paused`) попытки не переставляются — их id в списке дают 409 `QUEUE_ITEM_NOT_QUEUED`. Список, не совпадающий с текущими `queued` (головная уже стартовала, в хвост встала новая), — 409 `QUEUE_ORDER_STALE` с `details.unexpected`/`details.missing`; повторяющийся id — 422 `QUEUE_ORDER_DUPLICATE_IDS`. Аудит — `test_stand.queue_reordered`.

Попытка, которая ещё не исполняется (`queued`/`preparing`/`ready`/`paused`), переходит в `skipped`/`paused` сразу. Попытка в `running` физически идёт на стенде: ответ вернёт её же (state всё ещё `running`) с заполненным `interrupt_action` (`skip`/`pause`), а терминальный переход приедет от testing_worker'а, когда тот оборвёт сессию. Терминальная попытка не прерывается — 409 `QUEUE_ITEM_NOT_ACTIVE`. Стенд без остановленной попытки не продолжается — 409 `STAND_NOT_PAUSED`.

`skipped` — терминальное состояние без исхода: retry не заводится, СТП/Zephyr не обновляется, в счётчики обзора пула попытка не попадает. `paused` — не терминальное: попытка остаётся в очереди, стенд числится занятым (новые постановки просто встают за ней) и бронь сервера сохраняется. Кампания с остановленной попыткой остаётся `running`; кампания с пропущенными и успешными попытками закрывается как `partially_failed` — отдельного агрегатного статуса под пропуск нет.

### Фильтры истории попыток и логов

`GET /queue-items` дополнительно принимает `stand_id`, `test_id`, `attempt_id`, `retry_of_id`, `debug_mode`, повторяемый параметр `states` (`queued`/`preparing`/`ready`/`running`/`succeeded`/`failed`/`skipped`/`paused`), поиск `q` по коду/названию теста, ID попытки/стенда/сервера и `order=asc|desc`. Фильтры комбинируются через AND и всегда ограничены отделом пользователя. Символы `%` и `_` в поиске трактуются буквально.

Период `created_from` (включительно) / `created_until` (не включая) относится ко времени создания попытки. Оба значения — ISO datetime с обязательным часовым поясом. Обратный/пустой интервал отклоняется с 422 `INVALID_TIME_RANGE`. UI вводит период в MSK (`+03:00`).

`total` считается после фильтрации и до `limit`/`offset`. Порядок стабилизирован по ID при одинаковом времени. Список отдаёт `test_code`, `test_name` и `is_current`: наличие следующей попытки проверяется по всей цепочке, независимо от текущей страницы и фильтров.

Страница `/testing/logs` разделяет списки прогонов и одиночных запусков; фильтры и страница сохранены в URL. Из результата можно перейти к нужной попытке, а из неё — к предыдущей/следующей. Просмотр переиспользует существующие защищённые endpoints текста, сегментов, скачивания и WS; маскирование секретов не изменено. Поля `byte_offset_*` в существующем контракте сегментов обозначают индексы Unicode code points (Python-строки), UI учитывает это при нарезке кириллицы и emoji.

### Ядра ОС и новые рабочие зоны

- `POST /test-runs`: `kernel` необязателен. Без него backend берёт все ядра выбранной ОС; при пустом каталоге инициирует обнаружение в репозиториях. `kernels` в карточке кампании и `kernel` в каждом элементе состава фиксируют исходный набор. Явный `kernel` оставлен для совместимости старых клиентов; новый UI его не передаёт.
- `POST /stp/generate`: достаточно `os_version_id`. По умолчанию используется отдел пользователя, все обнаруженные ядра и оба режима `orel`/`smolensk`. Прежние явные параметры контекста поддерживаются. Генерация без ядра инициирует обновление каталога из репозиториев ОС. Ошибка поиска не начинает создание СТП с неполным набором ядер.
- Обнаружение вызывает `POST /api/server/v1/os-versions/{id}/resolve-kernels`. Для этого запроса применяется отдельный `OS_KERNEL_DISCOVERY_TIMEOUT_SECONDS` (120 секунд).
- Списки очереди дополнительно фильтруются по `os_version_id` и `kernel`. Ответы очереди и детали кампаний включают `log_status`: `available`, `rotated`, `pending`, `missing`. Ротация сохраняет `queue_items.log_rotated_at`, а текст и сегменты удаляет по прежней политике. Старые логи, удалённые до появления маркера, обозначаются как `missing`: достоверно установить причину их отсутствия без истории удаления нельзя.
- «Логи» — общая история в средней панели Shell, выбранный лог — рабочая зона справа. Крестик закрывает просмотр. В таблице прогона просмотр отсутствующего/ротированного лога отключён; результат и повтор остаются доступны. В общей истории видно объяснение отсутствия текста и предложение перезапуска последней попытки.

### Статистика: пересчёт и справочник семейств (D18)

Платформенные singleton-настройки внешнего сервиса статистики, индикатор фонового пересчёта и справочник семейств тестов для пер-категорийного пересчёта. Чтение — любой аутентифицированный актор; запись и ручной пересчёт — `(statistics_settings, *, update)`.

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/statistics/settings` | authenticated | `{enabled, base_url}`; дефолты, если строки нет. |
| `PUT` | `/statistics/settings` | `statistics_settings:update` | Частичное обновление. |
| `GET` | `/statistics/status` | authenticated | Индикатор: `status` (`idle`/`running`/`succeeded`/`failed`), `triggered_by`, `category` (текущее/последнее семейство), `categories` (весь выбранный набор, `null` — полный пересчёт), `started_at`, `finished_at`, `error`. |
| `POST` | `/statistics/recalculate` | `statistics_settings:update` | 202, фоновый пересчёт. Тело `{department_id?, category?, categories?: string[]}`. Без семейств — `/all-statistics`; с ними — выбранные семейства последовательно одной задачей (сбой одного не останавливает остальные). 422 `STATISTICS_CATEGORY_UNKNOWN` / `STATISTICS_CATEGORY_DISABLED` / `DEPARTMENT_ID_REQUIRED`. |
| `GET` | `/statistics/categories` | authenticated | Справочник в порядке `sort_order`: `{items: [{id, key, label, path, title_statistics, set_of_test_types, comparison_list, comparison_kernel_list, enabled, sort_order, ...}]}`. По умолчанию только включённые, `?include_disabled=true` — все. |
| `POST` | `/statistics/categories` | `statistics_settings:update` | 201. `key` — `[a-z][a-z0-9_]*`, UNIQUE (409 `STATISTICS_CATEGORY_DUPLICATE`), после создания не меняется. `path` начинается с `/`. |
| `PATCH` | `/statistics/categories/{id}` | `statistics_settings:update` | Частичное обновление без `key`; явный `null` в `comparison_list`/`comparison_kernel_list` очищает поле. 404 `STATISTICS_CATEGORY_NOT_FOUND`. |
| `DELETE` | `/statistics/categories/{id}` | `statistics_settings:update` | Hard-delete, `{ok: true}`. |

Одна строка справочника — один POST `{base_url}{path}` с телом `{title_statistics, username, token, set_of_test_types, latest_stable_versions_bool: true, comparison_list?, comparison_kernel_list?}`. Сид — восемь семейств легаси (`allta_app_full/allta_front.py:729-880`, `statistics_conf.py`). Автоматический пересчёт по завершении кампании не изменился — всегда полный.

## Тестовая учётка отдела

Логин, пароль и SSH-ключ пользователя исполнения теста на стендах отдела.
Хранятся в secret_service одной credential (`scope=service`,
`service=test_account`, `owner_dept_id=<отдел>`, `login` + JSON
`{"v":1,"password","private_key","public_key"}`), в
`department_test_settings.test_account_credential_id` — ссылка. Пароль и
приватный ключ в ответах не отдаются никогда. Изменения действуют со
следующей подготовки стенда (server_service раскрывает credential на шаге
`user_provision`, testing_service — на claim).

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/department-test-account/{department_id}` | `department_test_account:view` своего отдела или department_admin | Карточка: `configured`, `credential_id`, `credential_missing`, `login`, `login_hint` (легаси `test_username`), `has_password`, `ssh_public_key`, `home_template`, `home`. |
| `PUT` | `/department-test-account/{department_id}` | `department_test_account:update` своего отдела или department_admin | `{login?, password?, regenerate_ssh_key?, home_template?}`. Первый вызов заводит credential (пароль обязателен, SSH-пара Ed25519 генерируется), дальше — меняет только заданное. Запись в secret_service идёт bearer'ом вызывающего: нужен department_admin или admin secret_service отдела. |

**Error codes:** `PERMISSION_DENIED` (403), `CREDENTIAL_ACCESS_DENIED` (403, secret_service не дал записать), `TEST_ACCOUNT_PASSWORD_REQUIRED` (422), `TEST_ACCOUNT_INVALID` (422, credential в неожиданном формате), `TEST_ACCOUNT_CREDENTIAL_CONFLICT` (409), `SECRET_SERVICE_*` (503).

Очередь: без учётки элемент падает `failed_step=test_account`,
`error=TEST_ACCOUNT_NOT_CONFIGURED: …` — до брони стенда (старт цикла) либо
на claim, если ссылку сняли после подготовки.

## Вердикт из Zephyr

Исход теста — статус, который скрипт сам выставил тест-кейсу в прогоне
Zephyr (`run.py` всех веток выходит с 0). У теста
`test_definitions.verdict_source` = `zephyr` (по умолчанию) | `exit_code`.

Когда воркер сообщил `succeeded` у теста с `verdict_source=zephyr` и у
запуска есть прогон в Zephyr (`stp_test_run.zephyr_test_run_key`, кейс —
`stp_test_cases.zephyr_id` по коду теста, доступ — интеграции отдела),
item переходит в `awaiting_verdict`: стенд держится, очередь стоит. Фоновый
цикл раз в `zephyr_verdict_poll_seconds` читает `GET
/rest/atm/1.0/testrun/{key}` и переводит статус кейса по таблице отдела:
`passed` → `succeeded`, `failed` → `failed` (`failed_step=verdict`, дальше
обычный retry D13), `not_finished` — ждать. Не дождались за
`zephyr_verdict_wait_seconds` — итог `zephyr_verdict_unfinished_outcome`
(T3: `failed`, `error` «Тест не выставил итоговый статус в Zephyr…»). В
Zephyr сервис при `verdict_source=zephyr` ничего не пишет — только
локальную ячейку СТП.

Прогона в Zephyr нет (debug) — `verdict_without_zephyr_run`: `unknown` →
`succeeded` с `verdict=unknown` («результат не определён»), без СТП и
retry; `exit_code` — по коду выхода. Ненулевой код выхода, обрыв или
таймаут SSH — провал без ожидания.

В ответах очереди (`/queue-items`, очереди стендов): `verdict`
(`passed`/`failed`/`unknown`), `verdict_source`, `zephyr_status_raw`,
`verdict_resolved_at`; фильтр `states` принимает `awaiting_verdict`.

Настройки — в `GET/PUT /department-test-settings/{department_id}`:
`zephyr_verdict_wait_seconds` (2100), `zephyr_verdict_poll_seconds` (60),
`zephyr_verdict_unfinished_outcome` (`failed`), `verdict_without_zephyr_run`
(`unknown`).

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/zephyr-status-mappings/{department_id}` | свой отдел | `{department_id, is_default, items: [{zephyr_status, outcome}]}`; `is_default=true` — у отдела своих строк нет, действует набор по умолчанию («Pass»/«Fail»/«In Progress»/«Not Executed» и легаси-id 91/92/90/89). |
| `PUT` | `/zephyr-status-mappings/{department_id}` | `department_test_settings:update` или department_admin | `{items: [{zephyr_status, outcome}]}` (1–100, `outcome` ∈ `passed`/`failed`/`not_finished`, статусы уникальны без учёта регистра) — заменяет набор отдела целиком. |
| `DELETE` | `/zephyr-status-mappings/{department_id}` | то же | Удалить строки отдела — вернуться к набору по умолчанию. |

Статус, которого нет в таблице, считается `not_finished`.

## Профили запуска

`starter.sh`, клонирование ветки, пути на стенде, команды запуска и
остановки, pty и testenv — данные, а не код воркера. Правка содержимого —
всегда новая иммутабельная версия; `queue_items.launch_profile_version_id`
фиксирует, чем запущен item. Профиль теста: `test_definitions.launch_profile_id`,
иначе профиль отдела с `is_default`, иначе общий (`department_id: null`,
сид — легаси `allta_app_full/starter.sh`).

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/launch-profiles?department_id=` | свой отдел | Профили отдела и общие, с действующей версией. |
| `POST` | `/launch-profiles` | `launch_profile:update` или department_admin; общий — только по матрице | `{department_id, name, is_default, version}` — профиль и его первая версия. |
| `GET` | `/launch-profiles/{id}` | свой отдел | Профиль с действующей версией. |
| `PATCH` | `/launch-profiles/{id}` | как `POST` | `{name?, is_default?}`; `is_default=true` снимает флаг с прочих профилей отдела. |
| `GET` | `/launch-profiles/{id}/versions` | свой отдел | История версий, новые первыми. |
| `POST` | `/launch-profiles/{id}/versions` | как `POST` | Новая версия, становится действующей. `extra_files` — `[{path, content, mode, sensitive}]`: дополнительные файлы на стенде, путь — `{CODE}`, содержимое — `{{CODE}}`; без поля в теле наследуются от текущей версии (как `rerun_script`). |

Версия: `starter_script` (подстановки `{{CODE}}`), `clone` (`repo_url`,
`mode` branch/full, `depth`), `paths` (`script`, `dates`, `token`,
`testenv_marker`, `command_file`; `{CODE}`, в т.ч. `{TEST_HOME}`,
`{QUEUE_ITEM_ID}`), `launch_command_template` (токены по пробелам),
`stop_command_template` (`{{STARTER_PGREP_PATTERN}}`,
`{{STOP_GRACE_SECONDS}}`), `stop_grace_seconds`, `use_pty`, `testenv`
(`on_value`, `off_value`, `cleanup_other`). Значения claim'а в шаблонах:
`STARTER_PATH`, `DATES_PATH`, `GIT_TOKEN_PATH`, `TESTENV_MARKER_PATH`,
`COMMAND_FILE_PATH`, `GIT_REPO_URL`, `GIT_CLONE_ARGS`, `QUEUE_ITEM_ID`.
`rerun_script` — скрипт повторного запуска для шагов `run_mode=rerun`:
кладётся вместо `starter.sh` по пути `paths.script` и запускается той же
командой (аргументы — те же), без клонирования и файла токена. Новая
версия без поля `rerun_script` в теле наследует его от текущей.

**Error codes:** `LAUNCH_PROFILE_NOT_FOUND` (404), `LAUNCH_PROFILE_INVALID`
(422, профиль теста чужого отдела), `LAUNCH_PROFILE_NOT_CONFIGURED` и
`LAUNCH_PROFILE_PATH_INVALID`, `LAUNCH_PROFILE_RERUN_NOT_CONFIGURED` (провал
item'а на claim).

Настройки живого лога — `department_test_settings.log_chunk_interval_seconds`
(2.5) и `log_chunk_max_bytes` (4096), уходят воркеру в задании.

## Шаг настройки стенда и профили подготовки

У теста `stand_setup` (`kernel_cmdline_extra`, `script` с подстановками
`{{CODE}}`, `run_as` root/test_user, `phase` after_boot/before_kernel,
`reboot_after`, `timeout_seconds`) и `provisioning_profile_id`. При
постановке в очередь (до брони и restore) скрипт резолвится; неизвестная
переменная — провал item'а `failed_step=stand_setup`. В `prepare-for-test`
уходят `stand_setup` (отрезолвленный, `script_is_sensitive`) и
`provisioning` (значения профиля: теста → отдела по умолчанию → общего).

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/provisioning-profiles?department_id=` | свой отдел | Профили отдела и общие. |
| `POST` | `/provisioning-profiles` | `provisioning_profile:update` или department_admin; общий — только по матрице | `{department_id, name, is_default, allowed_failed_units, degraded_reboot_attempts, disable_pam_lastlog_inactive, boot_wait_timeout_seconds}`. |
| `PATCH` | `/provisioning-profiles/{id}` | как `POST` | Изменить поля, `is_default=true` снимает флаг с прочих профилей отдела. |

### Многостендовые сценарии (D12.2)

Сценарий — описание без исполнения (запуск отдельно): стенды пула и
упорядоченные действия `run_test` | `prepare_stand` | `wait`. Документ
сценария передаётся целиком; действие ссылается на стенд сценария по
`stand_id` стенда пула (стенд в сценарии не больше одного раза).

| Метод | Путь | Права | Описание |
|---|---|---|---|
| `GET` | `/scenarios?department_id=` | свой отдел | Список: `code`, `name`, `readiness`, число стендов и действий. |
| `POST` | `/scenarios` | `test_definition:create` в отделе сценария (или department_admin) | `{code, name, department_id, readiness, stp_test_case_code, stands: [{stand_id, label, preparation: full\|revert_only\|none, skip_pam_fix (bool, default false — без PAM-правки `pam_lastlog`, от `preparation` не зависит), provisioning_profile_id, stand_setup (C2), kernel_override, mode_override}], actions: [{kind, stand_id, test_id, is_verdict, params}]}`. 422: `SCENARIO_STAND_DUPLICATE`, `SCENARIO_STAND_INVALID` (нет стенда или чужой отдел), `SCENARIO_ACTION_INVALID`, `SCENARIO_ACTIONS_EMPTY`, `SCENARIO_TEST_INVALID` (тест чужого отдела), `SCENARIO_VERDICT_MISSING`, `SCENARIO_STP_VERDICT_AMBIGUOUS` (у сценария со `stp_test_case_code` не ровно одно `is_verdict`), `PROVISIONING_PROFILE_INVALID`; 409 `SCENARIO_DUPLICATE`, `SCENARIO_STP_CASE_TAKEN` (кейс СТП уже запускает другой сценарий отдела). `stp_test_case_code` — тест-кейс СТП, который запускает сценарий: кампания по СТП ставит `ready`-сценарий вместо одиночного теста. |
| `GET` | `/scenarios/{id}` | свой отдел | Сценарий целиком; у стендов — `target_type` (server/vm) и `stand_name`, у действий — `test_code`. |
| `PUT` | `/scenarios/{id}` | `test_definition:update` | Заменить сценарий целиком (те же проверки); отдел не меняется (`SCENARIO_DEPARTMENT_IMMUTABLE`). `stp_test_case_code` не передан — остаётся прежним, `null` — снять связь. |
| `DELETE` | `/scenarios/{id}` | `test_definition:delete` | 204. |
| `POST` | `/scenarios/{id}/preview` | свой отдел | `{os_version_id, kernel, debug}` → `actions[]`: для `run_test` — `launch` (ответ `launch-preview` теста на стенде действия, ядро/режим — `kernel_override`/`mode_override` стенда), для `prepare_stand` — `stand` (preparation, skip_pam_fix, ядро, режим, шаг настройки с маской), `errors` — ошибки действия. Адреса других стендов подставляет источник `stand_ref`. |

Удаление стенда пула (`DELETE /test-stands/{id}`), на который ссылается
переменная `stand_ref` или сценарий, — 409 `TEST_STAND_IN_USE` с
`referenced_by_variables`/`referenced_by_scenarios`.

### Запуск сценария

| Метод | Путь | Права | Описание |
|---|---|---|---|
| `POST` | `/scenarios/{id}/runs` | `test_definition:update` в отделе сценария; с `stp_test_run_id` — как обычный запуск по СТП: `test_run:create` на стенды сценария + членство кейса `stp_test_case_code` в этом столбце СТП по стенду действия-вердикта (ядро/режим — с override стенда) | `{os_version_id, kernel, mode, debug, stp_test_run_id}` → запуск. С `stp_test_run_id` вердикт пишется в ячейку СТП (`stp_status`); 422 `SCENARIO_STP_DEBUG_CONFLICT`, `SCENARIO_NOT_LINKED_TO_STP`, `SCENARIO_STP_VERDICT_AMBIGUOUS`, `STP_RUN_NOT_FOUND`, `TEST_NOT_IN_STP`. Бронь всех стендов «всё или ничего» (по порядку `stand_id`): не взялась хоть одна — взятые отпускаются, `state=waiting_for_stands`, `blocked_by=[{stand_id, reason}]`, повтор — фоновый тик. Затем параллельная подготовка (`full` — prepare-for-test; `revert_only` — prepare-for-test с `preparation=revert_only`: откат/restore, учётка и ядро без смены режима и без шага настройки; `none` — сразу; `skip_pam_fix=true` стенда уходит в prepare-for-test как `skip_pam_fix` и снимает PAM-правку при любом `preparation`), действия по `position`. `stands[]` запуска несут снимок `skip_pam_fix`. 422 `SCENARIO_REQUIRES_DEBUG` (сценарий или тест не `ready` без `debug`), `SCENARIO_NOT_RUNNABLE`, `SCENARIO_STAND_INVALID`. |
| `GET` | `/scenarios/{id}/runs` | свой отдел | Последние 50 запусков. |
| `GET` | `/scenario-runs/{id}` | свой отдел | `{state, launch_context, stp_test_run_id, test_run_id, current_position, wait_until, blocked_by, verdict, error, stands[{stand_id, state, kernel, mode}], actions[{position, kind, queue_item_id, state, verdict}]}`. |
| `POST` | `/scenario-runs/{id}/stop` | `test_definition:update` | skip текущего действия, отмена остальных, стенды отпускаются (`stopping` → `stopped`); 409 `SCENARIO_RUN_FINISHED`. |

Действия `run_test` — обычные `queue_items` (`scenario_run_id`,
`scenario_action_id`): логи, вердикт, skip — как у одиночного item'а;
ретраев нет, пауза — 409 `SCENARIO_ITEM_PAUSE_UNSUPPORTED`. Пока сценарий
держит стенд, одиночные постановки на него встают в очередь после сценария.

Internal: `POST /internal/stand-setup/{stand_setup_request_id}/completed`
(identity `server_service`) — исход «настройки без restore» перед шагом
многоступенчатого теста (сервер — `/internal/servers/{id}/stand-setup`, ВМ —
`/internal/vms/{id}/stand-setup`, без отката снимка): item сшивается по `correlation_id`
(`queue_items.stand_setup_correlation_id`), успех — `ready`, провал — провал
item'а (`failed_step` из callback'а); callback без ждущего item'а — 200, только
аудит `stand_setup.completed`.

У многоступенчатого теста `stand_setup` и `starter_suffix` живут в шагах теста; в
`/test-definitions` это значения первого шага.

## Шаги многоступенчатого теста

Тест исполняется шагами по порядку на одной брони стенда; одношаговый тест —
один шаг (заводится вместе с тестом). У шага: `name`, `starter_suffix`
(`$5` скрипта запуска; не задан при создании — как у первого шага),
`run_mode` (`full` — команда запуска профиля с клонированием; `rerun` —
`rerun_script` профиля), `stand_setup` (как у теста: у первого шага —
в `prepare-for-test`, у остальных — «настройка без restore» перед шагом) и
свои слоты команды. Доступ — как у слотов: чтение — видимость теста, запись —
`(test_definition, *, update)`.

| Метод | Path | Описание |
|---|---|---|
| `GET` | `/test-definitions/{test_id}/steps` | Шаги по порядку `position`. |
| `POST` | `/test-definitions/{test_id}/steps` | `{name?, starter_suffix?, run_mode? (rerun), stand_setup?, position?, copy_args_from_step_id?}` — новый шаг; `copy_args_from_step_id` копирует слоты шага этого теста. |
| `PATCH` | `/test-definitions/{test_id}/steps/{step_id}` | `{name?, starter_suffix?, run_mode?, stand_setup?}`; `stand_setup: null` — убрать. |
| `DELETE` | `/test-definitions/{test_id}/steps/{step_id}` | Удалить шаг со слотами. |
| `PUT` | `/test-definitions/{test_id}/steps/order` | `{step_ids}` — все шаги теста в новом порядке. |

Слоты шага: `GET /test-definitions/{test_id}/args?step_id=…`, `POST …/args`
с `step_id` в теле; без `step_id` — первый шаг. `POST …/args/copy-from`
принимает `step_id` (шаг текущего теста) и `source_step_id` (шаг источника).

В очереди: `queue_items.current_step_index`/`step_count` (в `PublicQueueItem`
— прогресс), задание воркеру несёт `step: {index, count, name}` (C3); в
контексте резолва — `STEP_INDEX` (с 1), `STEP_COUNT`, `STEP_NAME`. Вердикт —
после последнего шага; провал шага — провал item'а (ошибка начинается с
«Шаг N/M «имя»»); retry и resume начинают с первого шага.

**Error codes:** `TEST_STEP_NOT_FOUND` (404), `TEST_STEP_LAST` (409, последний
шаг не удаляется), `TEST_STEP_ORDER_STALE` (409), `TEST_STEP_ORDER_DUPLICATE_IDS`
(422), `TEST_STEP_FIRST_MUST_BE_FULL` (422 — первым шагом не может быть
`rerun`: после restore кода на стенде нет).

**Error codes:** `PROVISIONING_PROFILE_NOT_FOUND` (404),
`PROVISIONING_PROFILE_INVALID` (422, профиль чужого отдела).

## Публичный compat `/rest/api/*` (D17)

Легаси-пути ALLTA для скриптов на стендах — **вне** `/api/testing/v1`, без
авторизации, plain HTTP (HTTPS-guard их пропускает). Доступ — только из
включённых подсетей `compat_allowed_networks` (сид `10.177.103.0/24`), иначе
403 `LEGACY_COMPAT_SOURCE_FORBIDDEN`. IP источника — `request.client.host`,
за доверенным прокси (`TRUSTED_PROXY_IPS`) — правый недоверенный адрес
`X-Forwarded-For`. Лимит — `LEGACY_COMPAT_RATE_LIMIT` (120/minute) на IP,
глобальный лимит к этим маршрутам не применяется. Формат ответов — как у
легаси `allta_front.py:886-952`.

| Метод | Path | Ответ |
|---|---|---|
| `GET` | `/rest/api/get-jira-url` | `text/html`, `jira_base_url` отдела **без схемы** (`jira.astralinux.ru`). Отдел — по IP стенда, иначе отдел по умолчанию. |
| `GET` | `/rest/api/get-confluence-url` | то же для `confluence_base_url`. |
| `GET` | `/rest/api/get-repo-path` | `releases.json` (`attachment`): `{версия: [строки sources.list]}` из server_service. |
| `GET` | `/rest/api/get-repo-path-as-json` | тот же JSON без `attachment`. |
| `GET` | `/rest/api/get-astra-config` | `astra-config.json` (`attachment`), живой из `astra-qa-stand`. |
| `GET` | `/rest/api/get-box-config` | `box-config.json` (`attachment`), адрес FTP — из переменной `FTP_URL`. |
| `GET` | `/rest/api/get-testname-columns`, `/known-bugs`, `/annotations` | JSON, статичные словари. |
| `GET` | `/rest/api/get-stand` | `stand.html` (`attachment`). |
| `POST` | `/rest/api/available-kernels-from-{rc}` | JSON-список ядер версии `rc` (имя, не id). |
| любой | `/rest/api/<другое>` | 404 `{"error": "Not found", "message": ...}` (легаси-форма). |

**Error codes:** `LEGACY_COMPAT_SOURCE_FORBIDDEN` (403),
`LEGACY_COMPAT_DEPARTMENT_UNKNOWN` (404 — стенда с этим IP нет, отдел по
умолчанию не выбран), `LEGACY_COMPAT_URL_NOT_CONFIGURED` (404 — у выбранного
отдела поле пусто), `LEGACY_RC_NOT_FOUND` (404), ошибки server_service (503).

### Настройки compat (`/api/testing/v1/legacy-compat/...`)

Право — `(legacy_compat, view|update)` по матрице (сид — системная роль
`admin`), без bypass'а department_admin: настройка платформенная.

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/legacy-compat/settings` | `legacy_compat:view` | `{default_department_id, updated_by, updated_at}`. |
| `PUT` | `/legacy-compat/settings` | `legacy_compat:update` | `{default_department_id: str \| null}` — отдел для URL интеграций с незнакомого IP. |
| `GET` | `/legacy-compat/networks` | `legacy_compat:view` | `{items: [{id, cidr, description, enabled, ...}]}`. |
| `POST` | `/legacy-compat/networks` | `legacy_compat:update` | `{cidr, description?, enabled?}`; адрес без маски → `/32`, биты хоста — 422, дубль — 409 `COMPAT_NETWORK_DUPLICATE`. |
| `PATCH` | `/legacy-compat/networks/{id}` | `legacy_compat:update` | `{cidr?, description?, enabled?}`; 404 `COMPAT_NETWORK_NOT_FOUND`. |
| `DELETE` | `/legacy-compat/networks/{id}` | `legacy_compat:update` | Удалить подсеть. |
| `GET` | `/legacy-compat/resolve?ip=` | `legacy_compat:view` | `{ip, allowed, network_id, cidr, department_id, reason, stand_ids}` — что compat сделает с запросом с этого IP (`reason` = `stand` \| `default` \| `ambiguous` \| `no_default`); 422 `LEGACY_COMPAT_IP_INVALID`. |

Карта «IP → стенд» собирается из `test_stands` и connection-info
server_service и кешируется на `LEGACY_COMPAT_STAND_IP_CACHE_SECONDS` (60 с).

Адреса внешних сервисов легаси-хоста — static-переменные (для шаблонов
профиля запуска и скриптов): `INFOCOLLECTOR_URL`, `FTP_URL`, `DEVPI_URL`,
`DOCKER_REGISTRY`.

## Виртуальные стенды

Стенд — физический сервер (`target_type=server`, `server_id`; подготовка —
ACS restore) или ВМ server_service (`target_type=vm`, `vm_id`; подготовка —
откат снимка ВМ). Ровно одно из `server_id`/`vm_id` (CHECK на БД). Все вызовы
в server_service идут через `server_client` по цели стенда
(`services/stand_target.py`): бронь, `prepare-for-test`, `connection-info`,
смешанный `batch-status` (`server_ids` + `vm_ids`) — для ВМ по путям
`/internal/vms/{id}/…` (CONTRACTS C4).

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/test-stands` | `test_stand:create` | Тело: `target_type` (`server` по умолчанию / `vm`), `server_id` или `vm_id`, `legacy_token`, `queue_enabled`, `is_active`. Отдел — из живой карточки сервера/ВМ (`GET /servers/{id}` / `GET /vms/{id}` server_service с bearer'ом вызывающего). Не то сочетание полей — 422; дубль `server_id`/`vm_id` — 409 `TEST_STAND_DUPLICATE`. |
| `GET` | `/test-stands?vm_id=` | свой отдел | Фильтр по ВМ (как `server_id`). В ответах стенда — `target_type`, `server_id`, `vm_id`. |
| `GET` | `/test-stands/{id}/vm-snapshots` | свой отдел | Сопоставление «снимок ↔ версия ОС» ВМ-стенда: живой список server_service `{stand_id, vm_id, templates, snapshots[{name, version_name, normalized_version, mode, template, kind, is_current, …}]}`. Таблицы сопоставления нет (T7): server_service выбирает снимок тем же правилом. Не ВМ-стенд — 409 `TEST_STAND_NOT_VM`. |
| `GET` | `/pool-overview` | свой отдел | У стенда появились `target_type` и `vm_id`; `server_id` у ВМ-стенда `null`. |

При постановке на ВМ-стенд вместо проверки ACS-снимка — проверка снимка ВМ
(`GET /internal/vms/{id}/snapshots`): нет снимка, у которого
`normalized_version` (или версия как в имени) равна имени версии ОС из
`launch_context.RC` и режим из имени (шаблон с `{mode}`) совпадает с `MODE`
— 422 `VM_SNAPSHOT_NOT_FOUND` (`details`: `version_name`, `mode`,
`templates`, `snapshots`). Канал недоступен — проверка пропускается
(отказ придёт шагом `vm_revert`). `prepare-for-test` ВМ может закончиться
`failed_step=vm_revert` (снимок/откат/ВМ не поднялась) или `prepare` (не
вошли в гостя после отката). Учётные данные стенда
(`GET /test-stands/{id}/test-credentials`) у ВМ-стенда — всегда
`{"exists": false}`: ВМ готовится только с тестовой учёткой отдела.

