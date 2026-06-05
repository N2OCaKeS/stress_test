# loging_service

Централизованный сервис аудита платформы DBOS Server Manager. Принимает события от других сервисов (`auth_service`, `server_service`, `config_service`, `server_worker`), прогоняет через rule engine и retention, хранит в собственной PostgreSQL и отдаёт чтение администраторам и читателям своего департамента.

> Орфография `loging` без двойной `g` — намеренное имя каталога/БД в репо. В коде встречается префикс `logging.*` только для self-audit событий — это исторический шум, переименовывать не планируется.

## Что делает

- **Ingest.** `POST /api/logging/v1/events` принимает структурированные события от сервисов-источников по `Authorization: Bearer <SERVICE_API_KEYS[identity]>` + `X-Service-Identity: <service>`. Ключ выбирается из карты `SERVICE_API_KEYS` по advertised identity, сравнение timing-safe. Валидирует payload, нормализует Unicode, прогоняет через redaction-слой и rule engine.
- **Rule engine.** Цепочка правил на каждом событии: `OVERRIDE_SEVERITY` меняет уровень, `SUPPRESS` отбрасывает событие до записи, `ALLOW` сохраняет и прерывает цепочку. Match по `service`, `action` (glob `user.*`), `status`, `severity`, `allowed`. Реализация — `src/services/rule_service.py`.
- **Retention.** Per-severity / per-service политики в `retention_policies`. Фоновый sweep раз в сутки в 00:00 MSK; advisory-lock защищает от двойного срабатывания в multi-replica; DELETE чанкуется (commit на чанк), чтобы не лочить огромные выборки. `PUT` и `DELETE /retention` работают со ВСЕМ активным набором (при filtered-режиме это N×M строк). События самого `loging_service` ретеншном никогда не удаляются.
- **Storage.** Append-only таблица `audit_events`; партиционирование/ротация снаружи, средствами Postgres.
- **Read API.** `GET /events`, `GET /rules`, `GET /rules/{rule_id}`, `GET /services`, `GET /services/{service}/events`, `GET /retention` — для admin/reader. Dept-скоуп применяется автоматически для не-глобальных ролей. На `GET /events` точный `total` считается только при `?include_total=true` (иначе `total=null`, признак следующей страницы — `has_more`).
- **Write API.** `POST/PATCH/DELETE /rules` — `loging_admin` или `account_admin`; `PUT /retention` (replace) и `DELETE /retention` (idempotent) — только `loging_admin`. `POST /events` — service-to-service ingest по `SERVICE_API_KEYS`. `POST /services/{service}/events` — реестр event'ов от service-caller'а. `POST /token` — swagger-login проксируется в `auth_service`.

## Архитектура

- **Стек:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 15+, Alembic, slowapi, httpx.
- **Слои:** `api/v1/endpoints/` → `services/` → `repositories/` → `models/`. Pydantic-схемы в `schemas/`. Security-утилиты в `utils/` (`ids.py`, `redaction.py`, `normalization.py`).
- **Inter-service:** JWT через `introspect` в `auth_service` — pooled `AsyncClient`, lifespan-managed (`limits=20/10`, `timeout=3.0s`, `connect=2.0s`). Sync `httpx.post` fallback оставлен только под mock-тесты.
- **Развёртывание:** Docker compose локально, K8s в проде (2 реплики; advisory-lock защищает retention-loop от двойного срабатывания).

## Модель доступа

Авторизация захардкожена в коде; собственной матрицы прав и кастомных ролей у `loging_service` нет — права полностью определяются `platform_role` пользователя и его сервисными ролями в `loging_service`.

| Роль | Что может |
|---|---|
| `platform_role=loging_admin` | Управление правилами и retention, ingest-ключами. Чтение **всех** событий |
| `platform_role=account_admin` | Чтение **всех** событий аудита + **управление правилами** (CRUD `/rules`). **Без** управления retention |
| `platform_role=loging_reader` | Read-only, только свой департамент. **Требует `department_id`**: без него первое чтение событий → 403 `NO_DEPARTMENT` (by design, см. ниже) |
| `platform_role=department_admin` | Read-only, только свой департамент |
| `loging_service.reader` / `operator` / `admin` | Read-only, только свой департамент. Все три роли дают одно и то же — отдельных прав у service-`admin` тут нет |
| Сервисы-источники | Только **запись** через per-service `SERVICE_API_KEYS` map + `X-Service-Identity` |

Управление правилами (`POST/PATCH/DELETE /rules`) допускают и `loging_admin`, и `account_admin` (зависимость `require_admin_or_account_admin`). Управление retention-политиками (`PUT/DELETE /retention`) — **только у `platform_role=loging_admin`** (зависимость `require_admin`). Ни service-`admin`, ни `department_admin` в обе группы не пройдут. Все изменения правил и retention пишутся в собственный аудит сервиса.

Роли пользователей создаются и меняются **только в `auth_service`**; `loging_service` лишь читает их из JWT-introspect ответа.

### loging_reader vs loging_admin — dept-scope

`loging_reader` — это **dept-scoped reader**. Введён осознанно: оператор отдела видит аудит только своего отдела, а не всей платформы. Поэтому пользователь с `platform_role=loging_reader` **обязан** иметь `department_id`.

Если такой пользователь окажется без отдела — `auth_service` на `POST /users` его не пустит (`department_id` обязателен для всех platform-ролей, кроме `account_admin`), но даже при обходе на первом GET в `loging_service` сработает гард в `src/dependencies/auth.py` и вернёт **403 `NO_DEPARTMENT`**. Fallback «нет dept → показать всё» **намеренно не сделан**.

Если задача — дать сотруднику глобальный read-only по аудиту, выдай `platform_role=loging_admin` (видит все события + управляет правилами/retention) или `platform_role=account_admin` (read-only по всему аудиту, без управления правилами/retention). Эти platform-роли создаются без `department_id` — это их штатное состояние.

## Уровни severity

Шесть уровней: `TRACE` < `DEBUG` < `INFO` < `WARNING` < `ERROR` < `CRITICAL`. Назначаются автоматически по таблице `(action, status) → severity` в `src/services/rule_service.py::_DEFAULT_SEVERITY`. Для неизвестных action'ов: `status ∈ {failure, denied}` → `WARNING`, иначе `INFO`. Правила `OVERRIDE_SEVERITY` переопределяют без деплоя.

Дефолты для server-действий: `server.prepare` / `server.prepared` → `CRITICAL`; `server_account.provision` — success=`WARNING`, failure=`CRITICAL`; `server_account.update_on_host` → `INFO`; `server_account.deprovision` и `server_account.drift_detected` → `WARNING`.

Подробный справочник — `AUDIT_EVENTS.md`.

## Rule engine

Каждое событие на входе прогоняется по активным правилам по `priority DESC`:

1. Если severity не задана — берётся из `_DEFAULT_SEVERITY`.
2. `OVERRIDE_SEVERITY` — меняет severity, цепочка продолжается.
3. `SUPPRESS` — событие не сохраняется (эндпоинт возвращает 204).
4. `ALLOW` — событие сохраняется, цепочка прерывается (escape hatch из SUPPRESS).

> **Терминологический alias.** В корпоративной модели безопасности (§logging.3) этот эффект называется `DROP`. В коде, БД и API канон — `SUPPRESS`. API принимает оба варианта на вход (`effect="DROP"` нормализуется в `"SUPPRESS"`), но в ответах и в БД всегда хранится `SUPPRESS`. Это одно и то же.

Правила хранятся в `audit_rules`, кешируются in-memory с TTL 30 сек и `MAX(updated_at)`-проверкой — это даёт согласованность между несколькими репликами без `pub/sub`. CRUD по правилам инвалидирует кеш форсом. Кеш thread-safe через `threading.Lock`, при ошибке БД отдаёт stale-данные с логированием. Состояние кеша держится в `CacheState` (`UNLOADED` / `LOADING` / `READY` / `EMPTY`); сравнение «изменилась ли БД» идёт через `_last_db_max` — оба операнда приходят с writer-side, NTP-skew между pod'ами на сравнение не влияет. ORM-row'и снимаются в frozen `_RuleSnapshot` до выхода из session-скоупа, чтобы кеш не зависел от `Session` и lazy-loaded relationship'ов.

Match-полей пять: `match_service`, `match_action` (glob с одной звёздочкой на сегмент: `user.*` совпадает с `user.login`, не с `user.login.extra`), `match_status`, `match_severity`, `match_allowed`. `None` = «любое».

`match_action` валидируется против реестра `service_events` — нельзя завести правило на action, которого никто не регистрировал (только если реестр непустой; glob всегда разрешён).

`match_status` принимает `success` / `failure` / `denied` / `warning` — последний нужен для soft-mode гардов (например, `internal.dept_header_missing`).

## Retention

В БД политика хранения задаётся per-severity и per-service: колонки `severity`, `service` в `retention_policies` (`NULL` = «любой»), плюс `retain_days` (диапазон `30..3650`). В API:

- `RetentionPolicyCreate` принимает `retain_days` + опциональные `severity_filter: list[Severity] | None` и `service_filter: list[str] | None`. Repository пишет Cartesian (N×M строк) — одна row на каждую пару `(severity_i, service_j)`. Без фильтров — глобальная семантика (один row `NULL`/`NULL`).
- PATCH-эндпоинта нет: любое изменение идёт через full re-PUT (осознанный trade-off, упрощает replace-семантику).
- `PUT /retention` — replace-семантика: сначала деактивируется ВЕСЬ прежний активный набор (`deactivate_all_active`), затем пишется новый. Так сброс фильтров не оставляет старые узкие предикаты активными рядом с новой политикой. Unfiltered-PUT поверх существующей политики создаёт новую строку (новый `id`), а не in-place update.
- `DELETE /retention` (idempotent, 204) деактивирует ВЕСЬ активный набор — «отключить retention» гасит все активные строки (для filtered это N×M), чтобы фоновая ротация полностью остановилась. Self-audit несёт `deactivated_count` — реальный размер погашенного набора.

Daemon-thread (`src/main.py::_retention_loop`) считает время до следующего MSK 00:00 без `sleep(86400)`-дрейфа, берёт Postgres advisory-lock и итерирует активные политики — каждая выдаёт свой `DELETE` с предикатами. DELETE чанкуется (`DELETE ... WHERE id IN (SELECT ... LIMIT chunk)` с commit'ом на чанк), чтобы не держать row-locks на миллионы строк и не тормозить ingest. Инвариант: события `service='loging_service'` не удаляются никогда, даже если в фильтре пытаются их таргетировать (гард `_PROTECTED_SERVICE` в `apply_active`).

После успешного sweep'а сервис эмитит self-audit событие `logging.retention_sweep` с `{deleted_count, run_date_msk, policies: [{id, retain_days, severity, service}, ...], min_retain_days, max_retain_days}` — поля `min_retain_days`/`max_retain_days` опускаются, если на момент запуска sweep'а активных политик не было.

## Безопасность

- **Unicode normalization.** `utils/normalization.py` сворачивает входящие service-имена через NFKC + invisible-strip (ZWSP, ZWJ, BOM, SHY, …) + curated confusables fold (кир. `о` → ASCII `o`, греч. `ο` → `o`, и т.д.) до lower-кейса. Применяется в `EventCreate.service` валидаторе, `X-Service-Identity` парсере и path-параметре `/services/{service}/events`. Закрывает обход `loging_service`-reserved-guard через homoglyphs и retention-исключения.
- **Charset validators.** `actor_id` / `target_id` / `department_id` / `request_id` — `^[A-Za-z0-9_\-]{1,48}$` (request_id — до 64); `target_type` — `^[a-z_.]{1,64}$`; `action` — `^[a-z0-9_.]{1,128}$` (snake_case + точка + цифры для версий, напр. `provision_v2`, `http.4xx_error`); `service` после Unicode-нормализации — `[a-z_]{1,64}`; `username` — email-like `[A-Za-z0-9_\-@.]{1,128}`. Параллельно middleware санирует incoming `X-Request-ID` (стрипает CRLF/NUL, truncate, fallback `req_<hex>`).
- **Body-size middleware.** Отбивает `Content-Length > MAX_REQUEST_BODY_BYTES` (по умолчанию 1 MiB) **до** body-read'а. Malformed Content-Length (плюс, подчёркивания, юникод-digits, пробелы) → 400 `INVALID_CONTENT_LENGTH`. Если `Content-Length` нет (chunked transfer-encoding) — middleware считает байты в потоке и отдаёт 413 при превышении.
- **Shadow-keys guard.** `EventCreate.details` запрещает actor-identity ключи (`actor_id`, `actor_type`) на любой глубине вложенности — чтобы держатель ingest-ключа не shadow'ил identity actor'а через нестед `details`. Остальные top-level колонки (`service`, `action`, `status`, `request_id`, `severity`, `event_id`, `occurred_at`, `department_id`) разрешены внутри `details`: реальные события всех 4 сервисов используют их как target/scope/context (permission-аудит пишет `details={"action": "delete", ...}` — это granted action, не event action; denied-аудит несёт `department_id` цели и т.п.). NUL-byte в ключах/значениях тоже банится. Walker итеративный — рекурсия сама была бы DoS-вектором.
- **Depth-cap.** `details` ограничен глубиной 10 и размером 64 KB.
- **Per-service ingest auth.** `SERVICE_API_KEYS` JSON env — мапа `{identity: key}`; единственный режим service-to-service ingest (legacy shared `SERVICE_API_KEY` удалён). `X-Service-Identity` обязателен, работает ключом lookup'а; сравнение `secrets.compare_digest` — timing-safe. Identity вне map'а → 401 `INVALID_SERVICE_KEY`; mismatch ключа → 401 `INVALID_SERVICE_KEY`; отсутствие identity header'а → 401 `MISSING_SERVICE_IDENTITY`; пустой `SERVICE_API_KEYS` → 503 `SERVICE_TOKEN_NOT_CONFIGURED`. Compromise одного ключа даёт право писать только от имени соответствующего сервиса. На `POST /services/{service}/events` дополнительно сверяется path-параметр с identity → mismatch = 403 `SERVICE_IDENTITY_PATH_MISMATCH`.
- **HTTPS guard для introspect.** `AUTH_SERVICE_URL` валидируется на https в prod (`APP_ENV=production`), localhost-исключение для devcontainer. В prod + https-remote (non-loopback) запрещено `INTROSPECT_TLS_VERIFY=false` — fail-fast на старте. Production-guard'ы также требуют непустые `SERVICE_API_KEYS` и `INTROSPECT_SERVICE_API_KEY`, причём отдельные друг от друга (key-separation).
- **Idempotency.** `EventCreate.idempotency_key` (≤ 128 символов, opaque-токен в body) + partial UNIQUE `(service, idempotency_key) WHERE idempotency_key IS NOT NULL`. Repository делает `pg_insert(...).on_conflict_do_nothing(...)` и возвращает канонический row — outbox-retry safe: повторный POST с тем же ключом возвращает 201 с прежним `event_id` и `received_at`.
- **Redaction.** На стороне `loging_service` `event_service.record()` и `record_admin_action()` ещё раз прогоняют `details` через `utils/redaction.redact()` — defense-in-depth. Маскируются по имени ключа (`password`, `token`, `api_key`, `secret`, `credential`, …) и по форме значения (JWT-like, argon2/bcrypt-хэши).
- **Rate-limit на ingest.** `POST /events` ключуется per-service-identity (`X-Service-Identity`, нормализованный, с fallback на IP при отсутствии header'а), бюджет `INGEST_RATE_LIMIT` (`100/minute` по умолчанию). За k8s ingress общий per-IP bucket позволял одному сервису выжать бюджет остальных — теперь bucket'ы независимы. `headers_enabled=False` — `X-RateLimit-Remaining` не утекает атакующему.
- **Bypass-guard для self-audit.** `record_admin_action()` для собственных CRUD-операций пишет минуя `apply_rules` — нельзя выключить аудит rules/retention через SUPPRESS-правило.

## События, которые сервис эмитит сам

Self-audit события: `logging.events_queried`, `logging.rules_read`, `logging.rules_write`, `logging.services_read`, `logging.admin_access`, `logging.retention_read`, `logging.retention_write`, `logging.retention_sweep`, `logging.service_events_registered`, `logging_rule.create`, `logging_rule.update`, `logging_rule.delete`, плюс `audit.idempotency_conflict` (poisoning-guard warning при mismatch `payload_hash` на повторённом `(service, idempotency_key)`). Полный справочник — `AUDIT_EVENTS.md`.

## Отказоустойчивость

- 2 реплики API, retention-loop защищён Postgres advisory-lock.
- Собственный PostgreSQL-кластер из 2 экземпляров.
- На стороне сервисов-источников события публикуются через transactional outbox — at-least-once с дедупом через `idempotency_key` на стороне `loging_service`.

## Конфиг

| ENV | Default | Назначение |
|---|---|---|
| `APP_NAME` | `loging_service` | имя сервиса в собственных audit-эмитах |
| `APP_HOST` | `0.0.0.0` | bind-адрес uvicorn |
| `APP_PORT` | `8001` | |
| `APP_ENV` | `local` | `production`/`staging` включает доп. guard'ы (https, `verify=true`, непустые `SERVICE_API_KEYS` и `INTROSPECT_SERVICE_API_KEY`) |
| `APP_DEBUG` | `true` | в prod отбивается на старте, если `true` |
| `APP_LOG_LEVEL` | `INFO` | уровень логгера приложения |
| `DATABASE_URL` | `postgresql+psycopg://logging_user:...@localhost:5432/logging_db` | |
| `DB_POOL_SIZE` | `10` | SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | `20` | SQLAlchemy pool overflow |
| `SERVICE_API_KEYS` | `{}` | JSON map `{identity: bearer_secret}` для ingest. Обязателен непустой в prod; вне prod пустая map'а = ingest вернёт 503. |
| `AUTH_SERVICE_URL` | — | для JWT introspect; в prod https-only |
| `INTROSPECT_SERVICE_API_KEY` | — | ключ, которым `loging_service` сам ходит в introspect; обязателен в prod, должен отличаться от любого значения `SERVICE_API_KEYS` |
| `INTROSPECT_TIMEOUT_SECONDS` | `3.0` | таймаут `AsyncClient.post` к introspect (read/write бюджет) |
| `INTROSPECT_CONNECT_TIMEOUT_SECONDS` | `2.0` | отдельный connect-таймаут pooled introspect-клиента |
| `INTROSPECT_TLS_VERIFY` | `true` | `false` отбивается на старте в prod при https-remote |
| `INTROSPECT_POOL_MAX_CONNECTIONS` | `20` | `httpx.Limits` для pooled introspect-клиента |
| `INTROSPECT_POOL_MAX_KEEPALIVE` | `10` | `httpx.Limits` keepalive для pooled introspect-клиента |
| `TOKEN_PROXY_POOL_MAX_CONNECTIONS` | `10` | `httpx.Limits` для `/token` proxy-клиента (Swagger login) |
| `TOKEN_PROXY_POOL_MAX_KEEPALIVE` | `5` | `httpx.Limits` keepalive для `/token` proxy-клиента |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | body-size middleware cap (1 MiB) |
| `INGEST_RATE_LIMIT` | `100/minute` | slowapi default на `POST /events` |
| `AUDIT_QUERY_RATE_LIMIT` | `60/minute` | slowapi default на read-канал: `GET /events`, `GET /rules*`, `GET /services*`, `GET /retention` |
| `REGISTER_EVENTS_RATE_LIMIT` | `100/minute` | slowapi default на `POST /services/{service}/events` (per-identity) |
| `RATE_LIMIT_HEADERS_ENABLED` | `false` | включать ли `X-RateLimit-*` response headers |
| `SECURITY_HSTS_ENABLED` | `false` | `Strict-Transport-Security` header — только за https-фронтом |
| `AUDIT_DRAIN_TIMEOUT_SECONDS` | `2.0` | бюджет на draining pending self-audit задач при shutdown'е |
| `AUDIT_OUTBOX_ENABLED` | `true` | включать ли self-audit outbox drain-loop в lifespan'е |
| `AUDIT_OUTBOX_MAX_SIZE` | `4096` | bounded размер in-memory очереди self-audit; переполнение дропает старейший элемент в `dropped_overflow_total` |
| `AUDIT_OUTBOX_BATCH_SIZE` | `64` | сколько envelope'ов выгребает drain за одну транзакцию |
| `AUDIT_OUTBOX_POLL_INTERVAL_SECONDS` | `0.05` | пауза между батчами drain-loop'а |
| `AUDIT_COUNT_STATEMENT_TIMEOUT_MS` | `10000` | `SET LOCAL statement_timeout` для `COUNT(*)` в `GET /events?include_total=true`. На превышении (`57014`) репо возвращает `total=null`, страница рендерится. `0` — выключить guard. |
| `AUDIT_QUERY_STATEMENT_TIMEOUT_MS` | `30000` | `SET LOCAL statement_timeout` для основного `SELECT ... ORDER BY timestamp DESC OFFSET LIMIT` в `GET /events`. На превышении репо возвращает пустую страницу + warning лог, 200 без 500. `0` — выключить guard. |
| `RETENTION_LOOP_ENABLED` | `true` | запускать ли фоновый retention-cleanup daemon |

## Запуск

```bash
# Docker
docker-compose up --build

# Локально
PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8001
```

API: http://localhost:8001/docs

## Тесты

Актуальный счётчик — в выводе `make test-logging` (последняя зафиксированная цифра — `777 passed`, перепроверяется по `pytest --collect-only -q`). Реальный PostgreSQL в Docker, между тестами `TRUNCATE ... RESTART IDENTITY CASCADE` — никакого SQLite/моков БД. Реестр файлов и зон — в `Test.md`.

Покрытие: ingest, query, rules CRUD, rule_service unit (glob/severity/cache + stress), services registry, retention (+ protection + multi-replica + per-severity/per-service фильтры), admin/reader auth, payload validation, body-size limit + negative CL, rate-limit (per-IP + per-service), Unicode normalization, config validation (https, prod-guard `verify=false`), timezones, redaction, IDs, concurrency, schema validators (shadow-keys, NUL, charset, idempotency_key), introspect pool.

## Технологии

- Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 15+
- Alembic (миграции), slowapi (rate-limit), httpx (introspect)
- Docker, Kubernetes
