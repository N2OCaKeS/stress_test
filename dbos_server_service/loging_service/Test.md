# loging_service · реестр тестов

**Всего тестов**: точное число живёт в выводе `make test-logging` (последняя зафиксированная цифра — `777 passed`, актуальный счётчик перепроверяется по `pytest --collect-only -q`).
**Файлов**: точное число — `ls tests/test_*.py | wc -l` (карри-cover волны добавляли новые регрессионные файлы без сноса старых; на момент правки реестра — 66).
**Стек**: pytest + `fastapi.TestClient` поверх реального PostgreSQL в Docker (`loging_db_test`). Никаких моков БД — каждый тест начинается с `TRUNCATE` всех таблиц.

## Запуск

```bash
# Из корня dbos_server_service:
make test-logging      # все тесты в Docker
make test-build        # пересобрать тестовые образы

# Точечно:
docker compose -f loging_service/tests/docker-compose.test.yml run --rm test-runner \
    pytest tests/test_rules.py::TestRulesCRUD::test_create_rule -v
```

Точный счётчик и состав по тестам — `pytest --collect-only -q`.

---

## Состав файлов

| Файл | Что покрывает |
|---|---|
| `test_ingest.py` | `POST /events` — аутентификация ingest, валидация payload, severity-уровни, сохранение `details`, `/health` и `/ready` без auth. |
| `test_query.py` | `GET /events` — фильтры по department/service/severity/action/time-range, сортировка newest-first, пагинация. |
| `test_query_edge.py` | Edge cases для `GET /events`: комбинации фильтров, `from_time > to_time`, инвертированные диапазоны, границы пагинации (`limit=0`, отрицательный `offset`). |
| `test_rules.py` | CRUD по `/rules` + реальное применение правил на ingest: SUPPRESS, ALLOW, OVERRIDE_SEVERITY, glob, приоритет, admin-аудит CRUD-операций над правилами. |
| `test_rule_service.py` | Unit на `rule_service.py` без HTTP: `action_matches_pattern`, дефолты severity, `apply_rules` (SUPPRESS / ALLOW / OVERRIDE / комбинированные критерии). |
| `test_rule_engine_edge.py` | Edge-кейсы движка без БД: `_matches`, `apply_rules` через monkeypatched cache, early-return для `service="loging_service"`. |
| `test_rule_cache.py` | Unit на `_RuleCache`: TTL, `MAX(updated_at)`-перезагрузка, stale-fallback при ошибке БД, конкуррентный `get()`. |
| `test_rule_cache_stress.py` | Stress: 100+ параллельных `get()` ⇒ ровно одна перезагрузка, `get()` параллельно с `invalidate()`, отсутствие memory-leak при повторных загрузках. |
| `test_rule_patch.py` | `PATCH /rules/{id}`: переходы effect/effect_severity, валидация `match_action` против реестра, конфликт имён, инвалидация кеша. |
| `test_services.py` | `POST /services/{svc}/events` (регистрация известных action), `GET /services`, валидация `match_action` против реестра. |
| `test_retention.py` | API ретеншена `GET/PUT/DELETE /retention`: границы `30 ≤ retain_days ≤ 3650`, идемпотентность DELETE, admin-guard, per-severity / per-service колонки (`severity`, `service`) с CHECK-валидацией каталога severity и `RESERVED_SERVICE_NAMES`. |
| `test_retention_apply.py` | Сама функция `apply_active`: 0 удалений без политики, граница `cutoff`, rowcount возврата, неактивная политика. |
| `test_retention_protection.py` | Инвариант безопасности: события `service='loging_service'` не удаляются ретеншном ни при каких retain_days. |
| `test_admin_auth.py` | `require_admin` поверх admin-эндпоинтов, разделение per-service ingest map vs JWT, `POST /token` proxy в auth_service, проверка `Authorization: Bearer <INTROSPECT_SERVICE_API_KEY>` на outbound introspect-вызовах. |
| `test_reader_auth.py` | `require_reader`: `account_admin` без скоупа, dept-scoping для `loging_reader`/`department_admin`, сервисные роли в `loging_service`. |
| `test_admin_audit_severity.py` | `record_admin_action`: автоназначение severity из `_DEFAULT_SEVERITY`, обход SUPPRESS-правил, явный severity не перезаписывается. |
| `test_middleware.py` | Self-audit middleware в `main.py`: `_action_for_path`, `_http_status_to_category`, форма AppException-ответов, X-Request-ID, `_emit_audit` обходит правила. |
| `test_payload_validation.py` | Валидация `EventCreate`: лимит `details` 64 KB, `max_length` всех строковых полей, `actor_type` enum, сохранение опциональных полей. |
| `test_schema_validators.py` | Pydantic-схемы целиком: `EventCreate` (charset, shadow-keys, NUL-byte guard, `request_id`, `idempotency_key`, charset-валидаторы `target_id`/`target_type`/`actor_id`/`subject_id` против CRLF-инъекций и unicode-байпасса), `RuleCreate` (с `effect=WARNING` / `RuleStatus.warning`), `RegisterEventsRequest`. |
| `test_body_size_limit.py` | DoS-fix: middleware режет `Content-Length > MAX_REQUEST_BODY_BYTES` (default 1 MiB) до чтения body, malformed Content-Length (negative / plus / underscore / unicode-digit / whitespace) → 400 `INVALID_CONTENT_LENGTH`, chunked-overflow → 413 на стриме. |
| `test_rate_limit.py` | Per-IP `100/minute` на ingest (slowapi), отдельный bucket per-service, `headers_enabled=False`, отсутствие лимита на `/health`. |
| `test_redaction.py` | Defense-in-depth маскировка `details`: по имени ключа (password/token/secret/hash/credential) и по форме значения (JWT, argon2/bcrypt), вложенность, truncate длинных строк. |
| `test_normalization.py` | `utils.normalization.normalize_service_name`: NFKC-фолд, удаление невидимых символов, confusable-маппинг, защита от unicode-байпасса reserved-имени `loging_service`. |
| `test_timezones.py` | UTC в БД vs MSK на отображении: нормализация `timestamp` с offset, `received_at` всегда UTC, naive-input. |
| `test_config_validation.py` | Production-guard `Settings`: обязательный непустой `SERVICE_API_KEYS` (legacy shared `SERVICE_API_KEY` удалён), обязательный `INTROSPECT_SERVICE_API_KEY` отдельным значением от ingest-ключей, HTTPS для `AUTH_SERVICE_URL` в проде, запрет `INTROSPECT_TLS_VERIFY=false` на не-loopback. |
| `test_core_exceptions.py` | Unit на `core/exceptions.py`: HTTP-статусы и form ошибок, `_fetch_identity` на сетевые сбои (timeout/connection refused/5xx). |
| `test_ids.py` | Префиксы ID: `log_<hex>`, `rl_<hex>`, `se_<hex>`, `rp_<hex>`, уникальность на 1000 итераций, длина ≤ 48. |
| `test_concurrency.py` | Гонки upsert `service_events`: последовательный и параллельный upsert через `INSERT ... ON CONFLICT (service, action) DO UPDATE` — оба завершаются без `IntegrityError`. |
| `test_introspect_pool.py` | Pooled `AsyncClient` для introspect — закрывает slowloris (раньше каждый запрос открывал свежий TCP+TLS handshake к auth_service). |
| `test_batch2.py` | Сводный батч: проброс `actor_type` из introspect, idempotency-key на ingest (`(service, idempotency_key)` UNIQUE), per-service `SERVICE_API_KEYS` accept/reject, регрессионный гард на отсутствие legacy shared-key fallback'а. |
| `test_hardening.py` | Hardening-батч: whitelist `default_severity` + `RuleStatus.warning` как четвёртый литерал, charset-валидаторы service/action/username/target_id/target_type против CRLF-инъекций, retention advisory-lock и sleep-until-MSK-00:00, RESERVED_SERVICE_NAMES в `core/constants.py`. |
| `test_audit_events_md_sync.py` | Doc-sync guard: парсит `AUDIT_EVENTS.md` (HTTP middleware / retention / admin actions / severity-override таблицы) и для каждой `(action, status, severity)` строки сверяет с `_DEFAULT_SEVERITY` в `services/rule_service.py` — ловит drift между кодом и markdown. |
| `test_cov_focus.py` | Точечное покрытие гэпов: `_RuleCache` TTL под NTP-step (monkeypatch `time.monotonic` с прыжком), `event_service.apply_rules` на пересекающихся политиках (Cartesian OVERRIDE + SUPPRESS), retention `apply_active` chunked DELETE при rollback + filter-set sweep, `POST /events` `_ACTION_PATTERN` с цифрами и unicode-confusables, `core/limiter` `_rate_limit_key` exempt-path, dept-scoped roles quirk (`loging_reader` без `department_id` → 403 `NO_DEPARTMENT`). |
| `test_p4_cleanups.py` | P4-чистки: DB pool sizing через env (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`) вместо хардкода в `db/session.py`; introspect connect-таймаут через `INTROSPECT_CONNECT_TIMEOUT_SECONDS` вместо literal `2.0`; `self_audit_failures_total` под `threading.Lock` (lost-increment guard под параллельным `to_thread`'ом); `ADVISORY_LOCKS["retention_sweep"]` единственный источник pg-advisory-key. |
| `test_events_count_timeout.py` | `SET LOCAL statement_timeout` guard в `repositories/events.query`: `AUDIT_COUNT_STATEMENT_TIMEOUT_MS` (default 10000) ограничивает `COUNT(*)` под `include_total=true` — `57014` → `total=null` + warning; `AUDIT_QUERY_STATEMENT_TIMEOUT_MS` (default 30000) ограничивает основной `SELECT` — `57014` → пустая страница + warning, без 500. Оба под общим `_with_statement_timeout(...)` хелпером с SAVEPOINT, чтобы query_canceled не валил внешнюю транзакцию. |

---

## Инфраструктура

`tests/conftest.py` обеспечивает:

- **Тестовая БД** — `logging_db_test` в контейнере `loging-tests-test-postgres-1`. Схема создаётся раз на сессию через `Base.metadata.create_all`, между тестами `TRUNCATE audit_events, audit_rules, service_events RESTART IDENTITY CASCADE`.
- **`TestClient`** — синхронный, через `fastapi.testclient.TestClient`. Зависимость `get_db` подменяется на тестовую сессию.
- **Две фикстуры клиента**:
  - `client` — без аутентификации (для проверки 401/403 путей и для ingest через `auth_headers`). Выставляет `SERVICE_API_KEYS` map для всех 4 known identity на единый `TEST_API_KEY` и отдельный `INTROSPECT_SERVICE_API_KEY`.
  - `admin_client` — переопределяет `require_admin` И `require_reader` на `ADMIN_IDENTITY` (loging_admin), используется для всех админ-эндпоинтов.
- **`auth_headers`** — `{"Authorization": "Bearer test-service-api-key", "X-Service-Identity": "auth_service"}` для POST /events. Тесты, ходящие под другой identity, переписывают header руками.
- **Имитация identity через httpx mock** — для тестов с не-admin ролями (`patch("src.dependencies.auth.httpx.get")` → возвращает кастомный identity-payload).
- **Payload-factories** — `make_event(**overrides)`, `make_rule(**overrides)`, `make_event_def(**overrides)` с разумными дефолтами.

### Изоляция

В отличие от auth_service (SAVEPOINT), здесь используется явный `TRUNCATE` перед каждым тестом — потому что код loging_service делает `session.commit()` напрямую, и savepoint-патч не сработает. Минус — медленнее на больших таблицах, плюс — проще для понимания.

### Что НЕ покрыто

- **Фоновый `_retention_loop`** (`src/main.py`) — таймер MSK 00:00 не тестируется напрямую; сама функция `apply_active(db)` покрыта в `test_retention_protection.py` и `test_retention_apply.py`.
- **E2E-стек с реальным auth_service** и кросс-сервисным аудитом — лежит в `dbos_server_service/tests/` (cross-service integration).
- **Производительность ingest** — отдельный нагрузочный тест, в реестре нет.
- **Полная интеграция middleware self-audit через TestClient** — `_action_for_path`, `_emit_audit` и форма ответов покрыты unit-тестами; горячий путь middleware идёт через `AuditOutbox` (bounded `asyncio.Queue` + drain-loop, см. `services/audit_outbox.py`); сценарии outbox'а покрыты `test_audit_outbox*.py`, но end-to-end запись из реального middleware через очередь напрямую не воспроизводится.
