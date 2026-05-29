# Справочник событий аудита

Документ покрывает две стороны контракта `loging_service`:

1. **Что сервис принимает** — схема `EventCreate` для `POST /api/logging/v1/events`. Сюда попадают события от любого сервиса-источника (`auth_service`, `server_service`, `server_worker`, `config_service`).
2. **Что сервис эмитит сам** — 10 собственных событий `logging.*` / `logging_rule.*`.

Severity вычисляется автоматически в `src/services/rule_service.py::_DEFAULT_SEVERITY` по таблице `(action, status) → severity`, если поле не указано явно при ingest'е. Правила (`OVERRIDE_SEVERITY` / `SUPPRESS` / `ALLOW`) могут переопределить или подавить любую группу событий без деплоя.

---

## Схема EventCreate (что сервис принимает)

`POST /api/logging/v1/events`. Body — JSON, заголовки `Authorization: Bearer <SERVICE_API_KEY>` + `X-Service-Identity: <service>`.

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
| `details` | `dict` (JSONB) | нет (default `{}`) | Структурированные детали. Размер ≤ 64 KB, глубина ≤ 10. Reserved-keys (`actor_id`, `actor_type`) запрещены на любой глубине — чтобы держатель `SERVICE_API_KEY` не shadow'ил identity actor'а через `details`. NUL-byte в строках банится. Прогоняется через redaction-слой перед записью. |

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

Десять собственных событий. Все они идут через `record_admin_action()` (для admin CRUD) или `_emit_audit()` (для HTTP middleware), **минуя rule engine** — SUPPRESS-правило не подавит self-audit.

### HTTP middleware (`logging.*`)

Эмитятся на любой запрос (кроме `/health` и `/ready`) в `src/main.py::audit_access`. `action` определяется по `method + path` (`_action_for_path`).

| action | status | severity | Когда возникает | target_type |
|---|---|---|---|---|
| `logging.events_queried` | `success` | INFO | `GET /api/logging/v1/events` (любой фильтр) | `audit_event` |
| `logging.rules_read` | `success` | INFO | `GET /api/logging/v1/rules` или `GET /rules/{id}` | `audit_rule` |
| `logging.rules_write` | `success` | WARNING | `POST/PATCH/DELETE /api/logging/v1/rules*` (поверх `logging_rule.*`) | `audit_rule` |
| `logging.services_read` | `success` | INFO | `GET /api/logging/v1/services` или `GET /services/{svc}/events` | `service_event` |
| `logging.admin_access` | `success` | INFO | Любой admin-endpoint без явного маппинга в `_action_for_path` | — |
| `logging.retention_write` | `success` | WARNING | `PUT/DELETE /api/logging/v1/retention` — изменение или отключение политики хранения | `retention_policy` |
| `logging.retention_sweep` | `success` | INFO | Фоновый retention-цикл в 00:00 MSK после успешного `apply_active` | `audit_event` |

При не-2xx ответе action из `_action_for_path` подменяется на одно из:

| action | status | severity | Когда возникает |
|---|---|---|---|
| `http.access_denied` | `denied` | CRITICAL | HTTP 401 или 403 |
| `http.client_error` | `failure` | WARNING | HTTP 4xx (кроме 401/403) |
| `http.server_error` | `failure` | CRITICAL | HTTP 5xx |

Details: `{method, path, status_code, ip}` + при ошибке `error_code` если AppException.

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

### Retention CRUD и sweep (`logging.retention_*`)

Эмитятся в `src/api/v1/endpoints/retention.py::_audit` (PUT/DELETE) и
в `src/main.py::_retention_loop` (фоновый sweep). Идут через
`record_admin_action()`, минуя rule engine.

Details:

- `logging.retention_write` (PUT/DELETE): `{old: <snapshot|null>, new: <snapshot|null>}`,
  где snapshot — `{id, retain_days, description, is_active}` или `null`.
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
