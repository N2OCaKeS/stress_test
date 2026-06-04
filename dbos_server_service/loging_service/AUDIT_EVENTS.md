# Справочник событий аудита

Документ покрывает две стороны контракта `loging_service`:

1. **Что сервис принимает** — схема `EventCreate` для `POST /api/logging/v1/events`. Сюда попадают события от любого сервиса-источника (`auth_service`, `server_service`, `server_worker`, `config_service`).
2. **Что сервис эмитит сам** — собственные события `logging.*` / `logging_rule.*` / `services.*`.

Severity вычисляется автоматически в `src/services/rule_service.py::_DEFAULT_SEVERITY` по таблице `(action, status) → severity`, если поле не указано явно при ingest'е. Правила (`OVERRIDE_SEVERITY` / `SUPPRESS` / `ALLOW`) могут переопределить или подавить любую группу событий без деплоя.

---

## Схема EventCreate (что сервис принимает)

`POST /api/logging/v1/events`. Body — JSON, заголовки `Authorization: Bearer <SERVICE_API_KEYS[identity]>` + `X-Service-Identity: <identity>` (per-service ingest map; legacy shared `SERVICE_API_KEY` удалён).

| Поле | Тип | Обязательное | Описание |
|---|---|---|---|
| `timestamp` | `datetime` (ISO 8601 с tz) | да | Когда событие произошло у источника. Любая tz, хранится в UTC. Naive — допустим, помечается UTC. |
| `service` | `str` (≤ 64) | да | Сервис-источник. Должен совпадать с `X-Service-Identity` (на path-эндпоинтах). |
| `action` | `str` (≤ 128) | да | Имя action в формате `<object>.<verb>`. Реестр — `service_events`. |
| `actor_id` | `str` (≤ 48) | нет | ID пользователя/бота/сервиса, выполнившего action. |
| `actor_type` | `Literal["user","bot","service","anonymous","oauth_client"]` | нет (default `"user"`) | Тип actor'а. |
| `username` | `str` (≤ 128) | нет | Human-readable username для SIEM. |
| `department_id` | `str` (≤ 48) | нет | ID департамента, к которому относится actor. Используется для dept-scope на чтении. |
| `target_id` | `str` (≤ 48) | нет | ID объекта, над которым выполнялся action. |
| `target_type` | `str` (≤ 64) | нет | Тип объекта (`user`, `server`, `rule`, `oauth_client`, …). |
| `status` | `Literal["success","failure","denied","warning"]` | да | Исход action'а. `warning` — для soft-mode гардов: операция прошла (`allowed=True`), но что-то пахнет (missing header, dept mismatch без strict-fail). |
| `allowed` | `bool` | да | Было ли действие разрешено политикой доступа. |
| `severity` | `Literal["TRACE","DEBUG","INFO","WARNING","ERROR","CRITICAL"] \| None` | нет | Если `None` — берётся из `_DEFAULT_SEVERITY`. |
| `request_id` | `str` (≤ 64, `^[A-Za-z0-9_\-]+$`) | нет | Корреляция с HTTP-запросом. |
| `idempotency_key` | `str` (≤ 128) | нет | Outbox-retry safe: повторный POST с тем же `(service, idempotency_key)` вернёт 201 с прежним `event_id`. |
| `details` | `dict` (JSONB) | нет (default `{}`) | Структурированные детали. Размер ≤ 64 KB, глубина ≤ 10. Reserved-keys (`actor_id`, `actor_type`) запрещены на любой глубине — чтобы держатель ingest-ключа не shadow'ил identity actor'а через `details`. NUL-byte в строках банится. Прогоняется через redaction-слой перед записью. |

Ответ ingest'а: `201 {id: "log_<hex>", received_at: <UTC datetime>}` или `204 No Content` если SUPPRESS-правило отбросило событие.

---

## Уровни важности

Шесть уровней (по 1.txt §logging.2), от минимального к максимальному:

| Уровень | Типичное применение |
|---|---|
| `TRACE` | Зарезервирован для отладки, в коде по умолчанию не назначается |
| `DEBUG` | Зарезервирован для диагностики, в коде по умолчанию не назначается |
| `INFO` | Успешные операции чтения, list, получение данных |
| `WARNING` | Создание/управление ботами и токенами; мягкие нарушения доступа; rules CRUD по умолчанию |
| `ERROR` | Доступен для переопределения через правила; в коде по умолчанию не назначается |
| `CRITICAL` | Сброс пароля, ban, выдача OAuth-клиентов; ошибки аутентификации; admin update/delete |

Логика дефолта:

- Каждая пара `(action, status)` имеет явное значение в `_DEFAULT_SEVERITY` (`src/services/rule_service.py`).
- Для неизвестных action'ов: `status` ∈ {`failure`, `denied`} → `WARNING`, иначе → `INFO`.

---

## Соглашение об именовании action'ов

Формат `<object>.<verb>`. Составные имена объектов остаются в одном сегменте: `service_role`, `oauth_client`, `docker_registry`, `logging_rule`.

Примеры:
- `user.create`, `user.ban`, `user.password_reset`
- `pat.create`, `pat.revoke`
- `bot.token_create`, `bot.token_revoke`
- `group.service_grant`, `group.roles_assign`
- HTTP middleware: `http.access_denied`, `http.client_error`, `http.server_error`

Полный список событий, которые `auth_service` отправляет в `loging_service`, — в `dbos_server_service/auth_service/AUDIT_EVENTS.md`.

---

## События, которые `loging_service` эмитит сам

Собственные события идут через self-audit outbox (`audit_outbox.push_nowait` → фоновый `_drain_loop` → `write_envelope_to_db` → `record_admin_action(commit=False)`); под graceful-shutdown / до старта outbox'а используется sync fallback `_emit_audit_envelope`. Admin CRUD-endpoint'ы пишут напрямую через `record_admin_action()` в той же транзакции, что и основное изменение. Все пути **минуют rule engine** — SUPPRESS-правило не подавит self-audit.

### HTTP middleware (`logging.*`)

Эмитятся на любой запрос (кроме `/health`, `/ready` и `/token`) в `src/main.py::audit_access` — `_SKIP_AUDIT_PATHS`. `action` определяется по `method + path` (`_action_for_path`). Дополнительно скипается успешный (`status<400`) POST на `_INGEST_PREFIXES` (`/events`, `/services/`) — anti-amplification, иначе loging писал бы событие на каждое принятое; auth-провалы (401/403) и rate-limit (429) на этих путях всё равно эмитятся как `http.*`. Успешные `PUT/DELETE /retention` тоже скипаются (single source — endpoint-level `_audit` пишет `logging.retention_write`).

| action | status | severity | Когда возникает | target_type |
|---|---|---|---|---|
| `logging.events_queried` | `success` | INFO | `GET /api/logging/v1/events` (любой фильтр); путь `/services/{svc}/events` тоже сюда — `_action_for_path` проверяет `/events` раньше `/services` | `audit_event` |
| `logging.rules_read` | `success` | INFO | `GET /api/logging/v1/rules` или `GET /rules/{id}` | `audit_rule` |
| `logging.rules_write` | `success` | WARNING | `POST/PATCH/DELETE /api/logging/v1/rules*` (поверх `logging_rule.*`) | `audit_rule` |
| `logging.services_read` | `success` | INFO | `GET /api/logging/v1/services` (список зарегистрированных сервисов) | `service_event` |
| `logging.admin_access` | `success` | INFO | Любой admin-endpoint без явного маппинга в `_action_for_path` | — |
| `logging.retention_read` | `success` | INFO | `GET /api/logging/v1/retention` — чтение активных политик хранения | `retention_policy` |
| `logging.retention_write` | `success` | WARNING | `PUT/DELETE /api/logging/v1/retention` — изменение или отключение политики хранения | `retention_policy` |
| `logging.retention_sweep` | `success` | INFO | Фоновый retention-цикл в 00:00 MSK после успешного `apply_active` | `audit_event` |

При не-2xx ответе action из `_action_for_path` подменяется на одно из:

| action | status | severity | Когда возникает |
|---|---|---|---|
| `http.access_denied` | `denied` | CRITICAL | HTTP 401 или 403 |
| `http.client_error` | `failure` | WARNING | HTTP 4xx (кроме 401/403) |
| `http.server_error` | `failure` | CRITICAL | HTTP 5xx |

Details: `{method, path, status_code}` + `platform_role`, если у identity он есть. `ip` сейчас не пишется (см. `main.py::audit_access`).

### Admin actions (`logging_rule.*`)

Эмитятся в `src/api/v1/endpoints/rules.py::_audit` через `record_admin_action()`. Поля `actor_id`, `actor_type`, `username`, `department_id` берутся из identity (JWT introspect ответа).

| action | status | severity | Когда возникает | target_type |
|---|---|---|---|---|
| `logging_rule.create` | `success` | WARNING | `POST /api/logging/v1/rules` | `audit_rule` |
| `logging_rule.update` | `success` | CRITICAL | `PATCH /api/logging/v1/rules/{id}` | `audit_rule` |
| `logging_rule.delete` | `success` | CRITICAL | `DELETE /api/logging/v1/rules/{id}` | `audit_rule` |

Details:
- `create`: `{rule_id, rule_name}`.
- `update`: `{rule_id, changes: <PATCH-payload, exclude_unset>}`.
- `delete`: `{rule_id, rule_name}`.

### Idempotency conflict (`audit.idempotency_conflict`)

Эмитится в `src/services/event_service.py::_emit_idempotency_conflict_audit`, когда `POST /events` приходит с уже использованной парой `(service, idempotency_key)`, но `payload_hash` расходится с сохранённым — попытка poisoning'а. Идёт через `record_admin_action()`, минуя rule engine. Severity жёстко выставлен в payload'е.

| action | status | severity | Когда возникает | target_type |
|---|---|---|---|---|
| `audit.idempotency_conflict` | `warning` | WARNING | `POST /events` с конфликтом payload hash на повторённом `(service, idempotency_key)` | — |

Details: `{claimed_service, idempotency_key, claimed_action, error_code}`.

### Service-catalog registration (`logging.service_events_registered`)

Эмитится в `src/api/v1/endpoints/services.py::register_events` через `record_admin_action()` — каждый `POST /api/logging/v1/services/{service}/events` пишет self-audit в той же транзакции, что и upsert каталога. Caller — service-token holder, поэтому `actor_type=service`, `actor_id` — верифицированный `X-Service-Identity` (на legacy soft-mode без header — имя сервиса из path).

| action | status | severity | Когда возникает | target_type |
|---|---|---|---|---|
| `logging.service_events_registered` | `success` | INFO | `POST /api/logging/v1/services/{service}/events` | `service_event` |

Details: `{service, added, updated, total}` — счётчики из ответа upsert'а.

### Retention CRUD и sweep (`logging.retention_*`)

Эмитятся в `src/api/v1/endpoints/retention.py::_audit` (PUT/DELETE),
HTTP middleware `audit_access` для `GET /retention` (через `_action_for_path` → `logging.retention_read`) и
в `src/main.py::_retention_loop` (фоновый sweep). PUT/DELETE и sweep идут через
`record_admin_action()` напрямую; GET-средняя ветка кладёт envelope в
`audit_outbox.push_nowait`. Все пути минуют rule engine.

Details:

- `logging.retention_read` (GET): пишется middleware'ом — `{method, path, status_code}` (см. http-access выше).
- `logging.retention_write` (PUT): `{old: <group_summaries>, new: <group_summaries>}` — `_snapshot_list` группирует активные политики по `(retain_days, severity, description, is_active)` и схлопывает только сервис-измерение. Каждая группа — `{retain_days, severity, description, is_active, services: [...], count, sample_id}`. Filtered-PUT может породить Cartesian N×M политик; группировка не даёт `details` перевалить 64 KB cap `EventCreate._details_size`.
- `logging.retention_write` (DELETE): `{old: <group_summaries>, new: null, deactivated_count: int}` — `deactivated_count` несёт фактический размер погашенного активного набора (после filtered-PUT это N×M).
- `logging.retention_sweep`: `{deleted_count, run_date_msk, policies: [{id, retain_days, severity, service}, ...], min_retain_days, max_retain_days}`. Под filter-режимом активных политик может быть несколько с разными `retain_days` — массив `policies` несёт полный snapshot, `min_retain_days`/`max_retain_days` дают границы. Поля `min/max` опускаются, если на момент запуска sweep'а активных политик нет.

---

## События от других сервисов

`loging_service` принимает события от всех сервисов платформы. Каждый источник ведёт свой `AUDIT_EVENTS.md` со списком action'ов и default severity:

- `auth_service` — самый крупный писатель. Полный список — в `dbos_server_service/auth_service/AUDIT_EVENTS.md` (user/department/group/service_role/pat/bot/oauth_client/docker_registry/token/http).
- `server_service` — `dbos_server_service/server_service/AUDIT_EVENTS.md`. Дефолтные severity дублируются в `_DEFAULT_SEVERITY` loging-сервиса для следующих action'ов:
  - `server.delete` — CRITICAL
  - `server.prepare` — CRITICAL (success/failure)
  - `server.prepared` — CRITICAL (success/failure/denied; callback воркера)
  - `server.power_on` / `server.power_off` / `server.power_reboot` — WARNING (success/denied) / CRITICAL (failure)
  - `server.power_status` — INFO (success) / WARNING (failure/denied)
  - `server.inventory_sync` — INFO (success) / WARNING (failure/denied)
  - `server_account.users_inventory` — INFO (success) / WARNING (failure/denied)
  - `server_account.provision` — WARNING (success) / CRITICAL (failure)
  - `server_account.deprovision` — WARNING
  - `server_account.update_on_host` — INFO
  - `server_account.drift_detected` — WARNING (reconcile инвентаризации)
  - `server_account.password_rotate` / `ipmi_controller.password_rotate` — WARNING (success/denied) / CRITICAL (failure)
  - `server_account.password_revealed` / `ipmi_controller.credentials_revealed` — CRITICAL
  - `installed_packages.list` — INFO (success) / WARNING (failure/denied)
- `server_worker` — на сегодня собственного `AUDIT_EVENTS.md` нет; action'ы worker'а наследуют каталог `server_service` (worker эмитит те же `server.prepared` / `server_account.*` callback-события).
- `config_service` — не реализован, см. `obsidian/TODO.md`.

`loging_service` не валидирует `action` против чьего-либо whitelist'а на ingest'е — реестр `service_events` нужен только для правил (нельзя завести правило на незарегистрированный action). Зарегистрированные action'ы появляются при первом `POST /services/{service}/events` от источника.

---

## Изменение severity через правила

Значения по умолчанию из `_DEFAULT_SEVERITY` переопределяются правилами без деплоя:

```http
POST /api/logging/v1/rules
Authorization: Bearer <admin-jwt>

{
  "name": "escalate-pat-creation",
  "match_action": "pat.create",
  "effect": "OVERRIDE_SEVERITY",
  "effect_severity": "CRITICAL",
  "priority": 200
}
```

Эффекты:
- `OVERRIDE_SEVERITY` + `effect_severity` — изменить уровень, цепочка продолжается.
- `SUPPRESS` — не сохранять событие (эндпоинт вернёт 204).
- `ALLOW` — сохранить и прервать цепочку (escape hatch из SUPPRESS-правил).

> В 1.txt §logging.3 эффект «отбросить событие» назван `DROP`. В коде, БД и API он называется `SUPPRESS`. Это рассинхрон документации с реализацией; каноническое имя — `SUPPRESS`.

Доступные значения `effect_severity`: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

Критерии совпадения: `match_service`, `match_action` (glob `user.*` — одна точка), `match_status`, `match_severity`, `match_allowed`. `None` = «любое».
