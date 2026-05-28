# loging_service

Централизованный сервис аудита платформы DBOS Server Manager. Принимает события от других сервисов (`auth_service`, `server_service`, `config_service`, `server_worker`), прогоняет через rule engine и retention, хранит в собственной PostgreSQL и отдаёт чтение администраторам и читателям своего департамента.

> Орфография `loging` без двойной `g` — намеренное имя каталога/БД в репо. В коде встречается префикс `logging.*` только для self-audit событий — это исторический шум, переименовывать не планируется.

## Что делает

- **Ingest.** `POST /api/logging/v1/events` принимает структурированные события от сервисов-источников по `Authorization: Bearer <SERVICE_API_KEY>` + `X-Service-Identity: <service>`. Валидирует payload, нормализует Unicode, прогоняет через redaction-слой и rule engine.
- **Rule engine.** Цепочка правил на каждом событии: `OVERRIDE_SEVERITY` меняет уровень, `SUPPRESS` отбрасывает событие до записи, `ALLOW` сохраняет и прерывает цепочку. Match по `service`, `action` (glob `user.*`), `status`, `severity`, `allowed`. Реализация — `src/services/rule_service.py`.
- **Retention.** Per-severity / per-service политики в `retention_policies`. Фоновый sweep раз в сутки в 00:00 MSK; advisory-lock защищает от двойного срабатывания в multi-replica; DELETE чанкуется (commit на чанк), чтобы не лочить огромные выборки. `PUT` и `DELETE /retention` работают со ВСЕМ активным набором (при filtered-режиме это N×M строк). События самого `loging_service` ретеншном никогда не удаляются.
- **Storage.** Append-only таблица `audit_events`; партиционирование/ротация снаружи, средствами Postgres.
- **Read API.** `GET /events`, `GET /rules`, `GET /services`, `GET /retention` — для admin/reader. Dept-скоуп применяется автоматически для не-глобальных ролей. На `GET /events` точный `total` считается только при `?include_total=true` (иначе `total=null`, признак следующей страницы — `has_more`).

## Архитектура

- **Стек:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 15+, Alembic, slowapi, httpx.
- **Слои:** `api/v1/endpoints/` → `services/` → `repositories/` → `models/`. Pydantic-схемы в `schemas/`. Security-утилиты в `utils/` (`ids.py`, `redaction.py`, `normalization.py`).
- **Inter-service:** JWT через `introspect` в `auth_service` — pooled `AsyncClient`, lifespan-managed (`limits=20/10`, `timeout=3.0s`, `connect=2.0s`). Sync `httpx.post` fallback оставлен только под mock-тесты.
- **Развёртывание:** Docker compose локально, K8s в проде (2 реплики; advisory-lock защищает retention-loop от двойного срабатывания).

## Модель доступа

Авторизация захардкожена в коде; собственной матрицы прав и кастомных ролей у `loging_service` нет — права полностью определяются `platform_role` пользователя и его сервисными ролями в `loging_service`.

| Роль | Что может |
|---|---|
| `platform_role=loging_admin` | Управление правилами, retention, ingest-ключами. Чтение **всех** событий |
| `platform_role=account_admin` | Чтение **всех** событий аудита. **Без** управления правилами/retention |
| `platform_role=loging_reader` | Read-only, только свой департамент |
| `platform_role=department_admin` | Read-only, только свой департамент |
| `loging_service.reader` / `operator` / `admin` | Read-only, только свой департамент. Все три роли дают одно и то же — отдельных прав у service-`admin` тут нет |
| Сервисы-источники | Только **запись** через `SERVICE_API_KEY` + `X-Service-Identity` |

Управление правилами и retention-политиками — **только у `platform_role=loging_admin`**. Ни service-`admin`, ни `department_admin`, ни `account_admin` сюда не пройдут. Все изменения правил и retention пишутся в собственный аудит сервиса.

Роли пользователей создаются и меняются **только в `auth_service`**; `loging_service` лишь читает их из JWT-introspect ответа.

## Уровни severity

Шесть уровней: `TRACE` < `DEBUG` < `INFO` < `WARNING` < `ERROR` < `CRITICAL`. Назначаются автоматически по таблице `(action, status) → severity` в `src/services/rule_service.py::_DEFAULT_SEVERITY`. Для неизвестных action'ов: `status ∈ {failure, denied}` → `WARNING`, иначе `INFO`. Правила `OVERRIDE_SEVERITY` переопределяют без деплоя.

Дефолты для server-действий: `server.prepare` / `server.prepared` и `server_account.provision` (и success, и failure) → `CRITICAL`; `server_account.update_on_host` → `INFO`; `server_account.deprovision` и `server_account.drift_detected` → `WARNING`. Для `server_account.provision` каталог `_DEFAULT_SEVERITY` строже (CRITICAL), чем `default_severity` в самом `server_service` (WARNING) — расхождение известно, источник истины по severity-каталогу не зафиксирован.

Подробный справочник — `AUDIT_EVENTS.md`.

## Rule engine

Каждое событие на входе прогоняется по активным правилам по `priority DESC`:

1. Если severity не задана — берётся из `_DEFAULT_SEVERITY`.
2. `OVERRIDE_SEVERITY` — меняет severity, цепочка продолжается.
3. `SUPPRESS` — событие не сохраняется (эндпоинт возвращает 204).
4. `ALLOW` — событие сохраняется, цепочка прерывается (escape hatch из SUPPRESS).

> **Терминологический alias.** В корпоративной модели безопасности (§logging.3) этот эффект называется `DROP`. В коде, БД и API канон — `SUPPRESS`. API принимает оба варианта на вход (`effect="DROP"` нормализуется в `"SUPPRESS"`), но в ответах и в БД всегда хранится `SUPPRESS`. Это одно и то же.

Правила хранятся в `audit_rules`, кешируются in-memory с TTL 30 сек и `MAX(updated_at)`-проверкой — это даёт согласованность между несколькими репликами без `pub/sub`. CRUD по правилам инвалидирует кеш форсом. Кеш thread-safe через `threading.Lock`, при ошибке БД отдаёт stale-данные с логированием.

Match-полей пять: `match_service`, `match_action` (glob с одной звёздочкой на сегмент: `user.*` совпадает с `user.login`, не с `user.login.extra`), `match_status`, `match_severity`, `match_allowed`. `None` = «любое».

`match_action` валидируется против реестра `service_events` — нельзя завести правило на action, которого никто не регистрировал (только если реестр непустой; glob всегда разрешён).

`match_status` принимает `success` / `failure` / `denied` / `warning` — последний нужен для soft-mode гардов (например, `internal.dept_header_missing`).

## Retention

В БД политика хранения задаётся per-severity и per-service: колонки `severity`, `service` в `retention_policies` (`NULL` = «любой»), плюс `retain_days` (диапазон `30..3650`). В API:

- `RetentionPolicyCreate` принимает `retain_days` + опциональные `severity_filter: list[Severity] | None` и `service_filter: list[str] | None`. Repository пишет Cartesian (N×M строк) — одна row на каждую пару `(severity_i, service_j)`. Без фильтров — глобальная семантика (один row `NULL`/`NULL`).
- `RetentionPolicyUpdate` фильтр-полей не принимает: для смены filter'ов делается full re-PUT (осознанный trade-off).
- `PUT /retention` — replace-семантика: сначала деактивируется ВЕСЬ прежний активный набор (`deactivate_all_active`), затем пишется новый. Так сброс фильтров не оставляет старые узкие предикаты активными рядом с новой политикой. Unfiltered-PUT поверх существующей политики создаёт новую строку (новый `id`), а не in-place update.
- `DELETE /retention` (idempotent, 204) деактивирует ВЕСЬ активный набор — «отключить retention» гасит все активные строки (для filtered это N×M), чтобы фоновая ротация полностью остановилась. Self-audit несёт `deactivated_count` — реальный размер погашенного набора.

Daemon-thread (`src/main.py::_retention_loop`) считает время до следующего MSK 00:00 без `sleep(86400)`-дрейфа, берёт Postgres advisory-lock и итерирует активные политики — каждая выдаёт свой `DELETE` с предикатами. DELETE чанкуется (`DELETE ... WHERE id IN (SELECT ... LIMIT chunk)` с commit'ом на чанк), чтобы не держать row-locks на миллионы строк и не тормозить ingest. Инвариант: события `service='loging_service'` не удаляются никогда, даже если в фильтре пытаются их таргетировать (гард `_PROTECTED_SERVICE` в `apply_active`).

После успешного sweep'а сервис эмитит self-audit событие `logging.retention_sweep` с `{deleted_count, retain_days, run_date_msk}`.

## Безопасность

- **Unicode normalization.** `utils/normalization.py` сворачивает входящие service-имена через NFKC + invisible-strip (ZWSP, ZWJ, BOM, SHY, …) + curated confusables fold (кир. `о` → ASCII `o`, греч. `ο` → `o`, и т.д.) до lower-кейса. Применяется в `EventCreate.service` валидаторе, `X-Service-Identity` парсере и path-параметре `/services/{service}/events`. Закрывает обход `loging_service`-reserved-guard через homoglyphs и retention-исключения.
- **Charset validators.** `actor_id` / `target_id` / `department_id` / `request_id` — `^[A-Za-z0-9_\-]{1,48}$` (request_id — до 64); `target_type` — `^[a-z_.]{1,64}$`; `action` — `^[a-z0-9_.]{1,128}$` (snake_case + точка + цифры для версий, напр. `provision_v2`, `http.4xx_error`); `service` после Unicode-нормализации — `[a-z_]{1,64}`; `username` — email-like `[A-Za-z0-9_\-@.]{1,128}`. Параллельно middleware санирует incoming `X-Request-ID` (стрипает CRLF/NUL, truncate, fallback `req_<hex>`).
- **Body-size middleware.** Отбивает `Content-Length > MAX_REQUEST_BODY_BYTES` (по умолчанию 64 KB) **до** body-read'а. Negative Content-Length → 400 `INVALID_CONTENT_LENGTH`.
- **Shadow-keys guard.** `EventCreate.details` запрещает actor-identity ключи (`actor_id`, `actor_type`) на любой глубине вложенности — чтобы держатель `SERVICE_API_KEY` не shadow'ил identity actor'а через нестед `details`. Остальные top-level колонки (`service`, `action`, `status`, `request_id`, `severity`, `event_id`, `occurred_at`, `department_id`) разрешены внутри `details`: реальные события всех 4 сервисов используют их как target/scope/context (permission-аудит пишет `details={"action": "delete", ...}` — это granted action, не event action; denied-аудит несёт `department_id` цели и т.п.). NUL-byte в ключах/значениях тоже банится. Walker итеративный — рекурсия сама была бы DoS-вектором.
- **Depth-cap.** `details` ограничен глубиной 10 и размером 64 KB.
- **X-Service-Identity dual-mode.**
  - **Per-service mode:** `SERVICE_API_KEYS` JSON env — мап `{service: key}`; identity обязателен, проверяется `secrets.compare_digest` против ключа выбранного сервиса. Compromise одного ключа даёт право писать только от имени этого сервиса.
  - **Shared mode:** `SERVICE_API_KEY` один на всех (legacy/dev); identity сверяется с `KNOWN_SERVICE_IDENTITIES` set, soft mode (`STRICT_SERVICE_IDENTITY=true` — strict: unknown → 401).
  - На `POST /services/{service}/events` дополнительно сверяется path-параметр с identity → mismatch = 403 `SERVICE_IDENTITY_PATH_MISMATCH`.
- **HTTPS guard для introspect.** `AUTH_SERVICE_URL` валидируется на https в prod (`APP_ENV=production`), localhost-исключение для devcontainer. В prod + https-remote (non-loopback) запрещено `INTROSPECT_TLS_VERIFY=false` — fail-fast на старте. Пустой `INTROSPECT_SERVICE_API_KEY` при непустом `SERVICE_API_KEYS` тоже отбивается на старте.
- **Idempotency.** `EventCreate.idempotency_key` (≤ 128 символов, opaque-токен в body) + partial UNIQUE `(service, idempotency_key) WHERE idempotency_key IS NOT NULL`. Repository делает `pg_insert(...).on_conflict_do_nothing(...)` и возвращает канонический row — outbox-retry safe: повторный POST с тем же ключом возвращает 201 с прежним `event_id` и `received_at`.
- **Redaction.** На стороне `loging_service` `event_service.record()` и `record_admin_action()` ещё раз прогоняют `details` через `utils/redaction.redact()` — defense-in-depth. Маскируются по имени ключа (`password`, `token`, `api_key`, `secret`, `credential`, …) и по форме значения (JWT-like, argon2/bcrypt-хэши).
- **Rate-limit на ingest.** `POST /events` ключуется per-service-identity (`X-Service-Identity`, нормализованный, с fallback на IP при отсутствии header'а), бюджет `INGEST_RATE_LIMIT` (`100/minute` по умолчанию). За k8s ingress общий per-IP bucket позволял одному сервису выжать бюджет остальных — теперь bucket'ы независимы. `headers_enabled=False` — `X-RateLimit-Remaining` не утекает атакующему.
- **Bypass-guard для self-audit.** `record_admin_action()` для собственных CRUD-операций пишет минуя `apply_rules` — нельзя выключить аудит rules/retention через SUPPRESS-правило.

## События, которые сервис эмитит сам

Десять self-audit событий: `logging.events_queried`, `logging.rules_read`, `logging.rules_write`, `logging.services_read`, `logging.admin_access`, `logging.retention_write`, `logging.retention_sweep`, `logging_rule.create`, `logging_rule.update`, `logging_rule.delete`. Полный справочник — `AUDIT_EVENTS.md`.

## Отказоустойчивость

- 2 реплики API, retention-loop защищён Postgres advisory-lock.
- Собственный PostgreSQL-кластер из 2 экземпляров.
- На стороне сервисов-источников события публикуются через transactional outbox — at-least-once с дедупом через `idempotency_key` на стороне `loging_service`.

## Конфиг

| ENV | Default | Назначение |
|---|---|---|
| `APP_PORT` | `8001` | |
| `APP_ENV` | `development` | `production` включает доп. guard'ы (https, `verify=true`) |
| `DATABASE_URL` | `postgresql+psycopg://logging_user:...@localhost:5432/logging_db` | |
| `SERVICE_API_KEY` | `change-me-service-key` | shared secret для ingest (legacy, в prod не `change-me`) |
| `SERVICE_API_KEYS` | `{}` | JSON map per-service ключей; map непустой → mandatory identity + per-service `compare_digest` |
| `STRICT_SERVICE_IDENTITY` | `false` | unknown `X-Service-Identity` → 401 вместо WARNING |
| `AUTH_SERVICE_URL` | — | для JWT introspect; в prod https-only |
| `INTROSPECT_SERVICE_API_KEY` | — | ключ, которым `loging_service` сам ходит в introspect; обязателен в prod при непустом `SERVICE_API_KEYS` |
| `INTROSPECT_TIMEOUT_SECONDS` | `3.0` | таймаут `AsyncClient.post` к introspect |
| `INTROSPECT_TLS_VERIFY` | `true` | `false` отбивается на старте в prod при https-remote |
| `MAX_REQUEST_BODY_BYTES` | `65536` | body-size middleware cap |
| `INGEST_RATE_LIMIT` | `100/minute` | slowapi default на `POST /events` |

## Запуск

```bash
# Docker
docker-compose up --build

# Локально
PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8001
```

API: http://localhost:8001/docs

## Тесты

**777 passed** в актуальном прогоне (см. `Test.md` и `make test-logging`). Реальный PostgreSQL в Docker, между тестами `TRUNCATE ... RESTART IDENTITY CASCADE` — никакого SQLite/моков БД.

Покрытие: ingest, query, rules CRUD, rule_service unit (glob/severity/cache + stress), services registry, retention (+ protection + multi-replica + per-severity/per-service фильтры), admin/reader auth, payload validation, body-size limit + negative CL, rate-limit (per-IP + per-service), Unicode normalization, config validation (https, prod-guard `verify=false`), timezones, redaction, IDs, concurrency, schema validators (shadow-keys, NUL, charset, idempotency_key), introspect pool.

## Технологии

- Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 15+
- Alembic (миграции), slowapi (rate-limit), httpx (introspect)
- Docker, Kubernetes
