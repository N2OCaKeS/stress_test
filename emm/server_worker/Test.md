# server_worker · реестр тестов

**Всего тестов**: **1486 passed** в **27 файлах** под `server_worker/tests/`. Быстрый прогон (`make test-worker`) — ~3–5 мин (поднимает `test-postgres` и прогоняет alembic'ом схему `tasks`). Через `make test-dev-worker` — внутри devcontainer без перетягивания контейнеров.

**Стек**: pytest + `pytest-asyncio` + **реальный PostgreSQL** в Docker (`server_worker_db_test`). Redis — НЕ поднимается, taskiq-broker не дёргается живьём: `_runner.run_task` вызывается напрямую, scheduler/heartbeat-петли тестируются через monkeypatch на `asyncio.sleep`. HTTP к `server_service` / `loging_service` — `httpx.MockTransport`. BMC — `httpx.MockTransport` для Redfish и monkeypatch на `asyncio.create_subprocess_exec` для ipmitool. SSH — monkeypatch на `asyncssh.connect`.

Файл фиксирует структуру и состав групп. Точные имена тестов — снимок, могут эволюционировать; для актуальной картины — `pytest --collect-only -q`.

## Запуск

```bash
# Из корня dbos_server_service:
make test-worker          # всё в Docker
make test-dev-worker      # внутри devcontainer (быстрее)

# Точечно:
docker compose -f server_worker/tests/docker-compose.test.yml run --rm test-runner \
    pytest tests/test_retry_and_shutdown.py::TestMarkRunningCAS -v
```

`raw def test_*` через grep — 449. Pytest с `@pytest.mark.parametrize` разворачивает их до **1486 passed**.

## Раскладка по каталогам

| Каталог | Файлы | Tests (raw `def test_*`) | Зона |
|---|---:|---:|---|
| `tests/` | 19 | 326 | модель Task / runner / dispatch'еры / handlers / outbox / periodic / clients / smoke |
| `tests/unit/` | 8 | 123 | pure unit: redaction, config, constants, mapper, audit-client, server-service-client |
| **итого** | **27** | **449 → 1486 passed** | |

---

## tests/ — модель и инфраструктура

### `test_smoke.py` — sanity на импорты и регистрацию

Все модули `src.*` импортируются без побочки; taskiq-broker регистрирует полный набор tasks (`power.*`, `inventory.*`, `passwords.*`, `installed_packages.list`, periodic `tasks.cleanup_completed_old` / `audit_outbox.cleanup_published_old` / `worker.cleanup_stale_heartbeats` / `worker.heartbeat` / `worker.sweep`).

### `test_task_model.py` — ORM `Task`

Defaults (`status=queued`, `attempt=0`, `max_attempts=3`, `payload={}`), `enqueued_at` server_default=now(), UNIQUE по `idempotency_key` через `IntegrityError`, NULL idempotency_key не нарушает UNIQUE (PostgreSQL nullable-UNIQUE), наличие индексов из миграции.

### `test_runner.py` — `_runner.run_task` happy/failure/missing

Универсальный диспетчер: `load → mark_running (attempt++) → impl → mark_succeeded → audit success`. Failure-path: impl бросает → `mark_failed` с `f"{Type}: {message}"` в `last_error`, audit emit `status=failure`. `task_not_found` → audit без `mark_running`. Audit идёт через `audit_outbox` (publisher уже вызывает `audit_client.emit`, см. fixture `captured_audit`).

---

## tests/ — outbox и audit

### `test_audit_outbox.py` — transactional outbox

Lifecycle ломал audit-инвариант: `_runner.run_task` пишет audit-row в `audit_outbox` **в той же транзакции**, что `mark_succeeded/mark_failed`. Publisher (`run_publisher_loop`) отправляет в `loging_service` и проставляет `published_at`. Покрыто: at-least-once семантика, дубль publish безопасен (idempotency через `(task_id, action)`), whitelist `details.result` (не утекают сырые BMC-payload'ы), DLQ при превышении `max_attempts`, retry на 5xx, no-retry на 4xx, truncation `last_error` до `LAST_ERROR_MAX_LEN`.

### `test_audit_outbox_circuit_breaker.py` — adaptive sleep publisher'а

Решение об open/closed принимает shared `audit_publisher_breaker` в Redis (см. `unit/test_audit_publisher_breaker.py` про state-machine). `run_publisher_loop` после каждого прохода читает `get_state()`: open → sleep кап'нут `_CB_SLEEP_CHUNK_SECONDS` (или `retry_after`, что меньше); closed/half_open → обычный poll-interval. `_flush_outbox_once` всегда вызывается — отказ от HTTP'а делает `_publish_one.check()`, не loop.

### `unit/test_publisher_stdout_redaction.py` — redaction stdout-логов

Регрессия: ловля исключения в `run_publisher_loop` шла через `logger.error("...: %s", exc)`, секреты из URL credentials / Bearer-токенов утекали в stdout → fluentd/Loki. Теперь все `logger.error/exception` в publisher проходят через `redact_error_message`.

---

## tests/ — retry, sweep, scheduler, shutdown

### `test_retry_and_shutdown.py` — CAS, retry, graceful shutdown

`mark_running` CAS: повторный `run_task` на terminal-task не сбрасывает статус, impl не зовётся, audit пишет `duplicate_dispatch`. CAS-rejection при гонке между worker'ами. Retry-on-failure: `attempt < max_attempts` → `status=queued`, `scheduled_retry_at = now + backoff`. Exponential backoff с jitter. Graceful shutdown: SIGTERM ждёт окончания `RUNNING_TASKS` (`drain` timeout). Scheduler wiring и heartbeat-label.

### `test_durable_retry_and_sweep.py` — durable retry, orphan sweep, periodic

Durable retry: `tasks.scheduled_retry_at` пишется ДО fire-and-forget `_schedule_retry`. Worker-startup `_recover_scheduled_retries` подхватывает `status='queued' AND scheduled_retry_at <= now()` и kiq'ает в broker — гарантия, что после SIGKILL retry не теряется. Sweep: `worker.sweep` находит `RUNNING` task'и без живого heartbeat (`worker_heartbeats` без свежего row для `worker_id`), помечает `FAILED` с `last_error='orphan_sweep'`. Heartbeat: периодический upsert `worker_heartbeats(worker_id, last_seen=now())`.

### `test_quality_batch.py` — quality-фиксы

`LAST_ERROR_MAX_LEN` вынесен в `core/constants.py`, применяется в обоих publisher-truncation'ах. `worker.cleanup_stale_heartbeats`: periodic удаляет row'ы старше cleanup-порога, оставляет свежие. Bug-fixes в редких race-условиях между sweep и normal completion.

### `test_retention_cleanup_tasks.py` — bounded growth БД

`tasks.cleanup_completed_old`: DELETE `SUCCEEDED/FAILED` старше N дней (cron 03:00 MSK), не трогает `QUEUED/RUNNING`, не трогает свежие completed. `audit_outbox.cleanup_published_old`: DELETE published row'ы (`delivered` + DLQ) старше N дней (cron 03:30 MSK), unpublished не трогает.

---

## tests/ — task handlers

### `test_task_handlers.py` — happy-path всех handlers

`power.on/off/reboot/status` + `inventory.collect` + `passwords.ipmi_rotate_password` + `passwords.account_rotate_password`. HTTP к `server_service` и iDRAC/SSH замочены — фокус на: правильность `audit_action`/`target_type`, передача credentials и `server_id` в правильные mock'и, mark_failed + audit failure при ошибках фетча credentials, `_generate_password` даёт уникальные строки.

### `test_power_tasks_redfish.py` — power.* через RedfishClient

В отличие от `test_task_handlers.py`, не подменяется `_build_client` заранее — собран реальный `RedfishClient` поверх `httpx.MockTransport`. Покрыт actual httpx-flow внутри handler'а + Redfish error-mapping → `AppException(BMC_*)` → audit failure.

### `test_ipmitool_integration.py` — power.* / ipmi.rotate_password через ipmitool fallback

Сценарий: Redfish-probe (HEAD `/redfish/v1/`) проваливается, `get_bmc_client` отдаёт `IpmitoolClient`, handler говорит с BMC через subprocess. Покрыто: корректный проход через `dispatch_power_action` / `dispatch_rotate_user_password`, маппинг stderr-маркеров в `BMC_AUTH_FAILED` / `BMC_TIMEOUT` / `BMC_UNREACHABLE` через `_wrap_ipmitool_error`.

### `test_inventory_task.py` — `tasks/inventory.py` end-to-end

Реальный `SshClient` (mock'нутый через `asyncssh.connect`) + реальный submit-callback в `server_service`. Happy-path: handler склеивает credentials → SshClient → `submit_inventory_facts`. Auth-failure (`CredentialFetchError` → `mark_failed`). `audit details` содержит `server_id` и не утекает passwords.

### `test_account_password_task.py` — `passwords.account_rotate_password`

Реальный `SshClient` через mock asyncssh + submit_rotated_password. Round-trip: `fetch_account_password → SshClient.set_password (chpasswd) → submit_rotated_password (новый ciphertext server_service'у)`. Покрыто: chpasswd happy/fail, invalid login, stderr-scrubbing, идемпотентность fetch+submit.

---

## tests/ — BMC и transport clients

### `test_redfish_client.py` — `src/clients/redfish.py`

`httpx.MockTransport`, никакого реального BMC. Happy-path для каждого high-level метода (`get_chassis_power_status`, `set_chassis_power`, `set_boot_device`, `change_user_password`). 4xx/5xx → `RedfishError` с корректным `status_code` и `redfish_error_code` из `error.@Message.ExtendedInfo`. Edge: пустой ExtendedInfo, малформированный JSON, retry-after.

### `test_ipmitool_client.py` — `src/clients/ipmitool.py`

`asyncio.create_subprocess_exec` подменяется на async-функцию, возвращающую fake-Process с заранее заданными `returncode` и `(stdout, stderr)`. Покрыто: `chassis power status/on/off/reset/cycle`, `user set password`, timeout и IO, базовый args-набор, маскирование пароля в argv (для audit/logs), stderr-redaction.

### `test_ssh_client.py` — `src/clients/ssh.py` (asyncssh)

Lifecycle: `connect`, double-connect idempotent, `close`, async-cm. `run`: happy, sudo wrapping, timeout, auth-fail mapping (`BMC_AUTH_FAILED`-аналог для SSH). `set_password`: chpasswd happy, chpasswd-fail, invalid login, stderr scrubbing. `get_inventory`: lscpu/lsblk JSON, `/etc/os-release` parse, частично-битые блоки.

---

## tests/unit/ — pure unit

### `unit/test_audit_client.py` — `src/services/audit_client.py`

Payload содержит обязательные поля (`action`, `status`, `allowed`, `actor_type=service`, `service=server_worker`, `timestamp` ISO). Опциональные поля попадают только когда переданы. `Authorization: Bearer <api_key>` из `logging_service_api_key` (НЕ fallback на `worker_bot_token` — то PAT для `server_service`). 4xx/5xx → `AuditEmitError`, httpx сетевые ошибки → `AuditEmitError`. Timeout настраиваемый.

### `unit/test_server_service_client.py` — `src/services/server_service_client.py`

200 → JSON, не-200 → типизированный `CredentialFetchError` (с кодом `SERVER_SERVICE_*`). `httpx.HTTPError` → `SERVER_SERVICE_UNREACHABLE`. PAT (`worker_bot_token`) в Authorization, пустой токен → пустые headers. `submit_rotated_password` НЕ логирует plaintext в `exception.details`.

### `unit/test_audit_client.py` + `unit/test_redaction.py` — redaction

`redact_error_message`: URL credentials (`http://user:pass@host`), shell-флаги (`-U/-P`), `password=...`, `Bearer ...`, JWT, опаковые `dbos_pat_*` / `dbos_bot_*`. Безсекретные сообщения остаются как есть. Truncation > 2048, non-string input защищён. Интеграция в `_runner`: после throw в `task.last_error` и audit `details.error` нет секретов.

### `unit/test_inventory_mapper.py` — `services/ssh_client.py::inventory_facts_to_payload`

Парсер raw `SshClient.get_inventory()` (lscpu/lsblk JSON + `/etc/os-release` parse) → flat-схема `InventoryCallbackRequest` `server_service`'а. Happy-path + частично-битые блоки + полностью-пустой ответ.

### `unit/test_config_redis_auth.py` — `Settings.redis_url`

Фикс «Redis без AUTH → privilege escalation через RPUSH». DSN с password (`redis://:<pwd>@host:port/db`) принимается. Production-validator отвергает DSN без auth-сегмента. Не использует `get_settings()`, чтобы обойти module-cache.

### `unit/test_constants.py` — `TaskKind` / `TaskStatus`

Инварианты enum: имена, значения, нет дублей, mapping handler'ов покрывает все `TaskKind`.

### `unit/test_logging_config.py` — подавление verbose-логов клиентов

Регрессия: при `WORKER_LOG_LEVEL=DEBUG` `httpx._client` логировал полные запросы вместе с `Authorization: Bearer <worker_bot_token>` в stdout → journald. После импорта `src.main` уровень `httpx`, `httpcore`, `hpack` ≥ WARNING.

---

## Инфраструктура

`tests/conftest.py` обеспечивает:

- **Тестовая БД** — `server_worker_db_test` в контейнере `tests-test-postgres-1`. Создаётся раз на сессию (`DROP SCHEMA public CASCADE` → `alembic upgrade head`).
- **Изоляция тестов** — между тестами `TRUNCATE tasks CASCADE`, `TRUNCATE audit_outbox RESTART IDENTITY CASCADE`, `TRUNCATE worker_heartbeats CASCADE` + reset процесс-локального `RUNNING_TASKS` (`reset_for_tests()`). Это нужно потому что код воркера делает `session.commit()` напрямую — SAVEPOINT-подход (как у `auth_service`) не работает.
- **Готовые фикстуры**:
  - `make_task(task_kind, target_server_id, payload, created_by, request_id)` — создаёт row в `tasks` и возвращает `tid`.
  - `fetch_task(task_id)` — возвращает текущее состояние `Task`-row.
  - `captured_audit` — monkeypatch на `audit_client.emit` и `audit_outbox_publisher.audit_client.emit`, складывает события в list. Тесты проверяют `captured_audit[i]["action"]` и `details`.
- **Env-init до импорта `src.*`** — `Settings` строгий, поэтому `DATABASE_URL` / `REDIS_URL` / `SERVER_SERVICE_URL` / `AUTH_SERVICE_URL` / `LOGGING_SERVICE_URL` / `WORKER_BOT_TOKEN` ставятся в `os.environ` ДО любого `import src.*`.

## Ключевые наборы (cross-cutting)

- **BMC dispatcher** (`_bmc_helpers.get_bmc_client` + `dispatch_*`): Redfish-probe, fallback на ipmitool, error-unwrap → `AppException(BMC_*)` — `test_power_tasks_redfish.py`, `test_ipmitool_integration.py`, `test_task_handlers.py`.
- **Hardware-клиенты**: `test_redfish_client.py`, `test_ipmitool_client.py`, `test_ssh_client.py`.
- **Audit outbox**: at-least-once запись, publisher loop, DLQ, circuit breaker — `test_audit_outbox.py`, `test_audit_outbox_circuit_breaker.py`, `unit/test_publisher_stdout_redaction.py`.
- **Periodic tasks**: `cleanup_completed_old` + `cleanup_published_old` + `cleanup_stale_heartbeats` — `test_retention_cleanup_tasks.py`, `test_quality_batch.py`.
- **Heartbeat / sweep / scheduler / shutdown**: `test_retry_and_shutdown.py`, `test_durable_retry_and_sweep.py`.

## Что НЕ покрыто

- **Реальный Redis / taskiq broker** — `_runner.run_task` зовётся напрямую, kiq-роутинг и serialize/deserialize не тестируются end-to-end (broker регистрируется только smoke-тестом).
- **Реальный BMC / реальный SSH-сервер** — всё mock'ается. E2E с настоящим iDRAC или asyncssh-сервером лежит за пределами этого реестра.
- **Производительность / propagation latency** между outbox и `loging_service` — отдельный нагрузочный тест, в реестре нет.
