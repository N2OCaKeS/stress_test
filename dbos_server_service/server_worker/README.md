# server_worker

Async task runner для `server_service`-операций с длинным сетевым I/O:

- iDRAC/iLO power management (on/off/reboot)
- SSH inventory sync (OS version, packages)
- Periodic power/status polling через BMC
- Ротация паролей серверных аккаунтов и IPMI
- Live installed-packages listing через SSH

## Stack

- Python 3.12, asyncio + SQLAlchemy 2.0 async + Alembic
- [taskiq](https://taskiq-python.github.io/) + `taskiq-redis` — async task queue, Redis broker
- Собственная PostgreSQL БД `dev_server_worker` (history тасков: queued/running/succeeded/failed)
- `httpx` для server_service (decryption + rotate) и loging_service (audit)

У worker'а **нет публичного HTTP API**. Tasks публикуются в Redis из server_service (или любого internal-продьюсера); history хранится в собственной `tasks` таблице.

## Security — Redis AUTH обязателен

Worker слушает taskiq-очередь в Redis. Без AUTH'а любой контейнер в той же сети может `RPUSH` payload и инициировать `power-cycle` или ротацию паролей от имени worker'а (privilege escalation). Поэтому:

- В `docker-compose.dev.yml` redis поднимается с `--requirepass` (`REDIS_PASSWORD`, default `dev-redis-password` — только для локалки).
- `REDIS_URL` в `server_worker` и `server_service` имеет вид `redis://:<password>@redis:6379/0`.
- В `production` (`APP_ENV=production`) `core/config.py` падает на старте, если `REDIS_URL` не содержит password-сегмент.
- `.env.example` рядом — обязательно меняй `REDIS_PASSWORD` на длинный случайный перед публикацией.

## Worker_bot PAT — least-privilege

Worker ходит в server_service не как глобальный admin. Seed выдаёт ему role `worker_bot` с 4 entity-grant'ами:

| Сущность | Action'ы |
|---|---|
| `server_account` | `view_password`, `rotate_password` |
| `ipmi_controller` | `view_credentials`, `rotate_credentials` |

Никаких `power.{on,off,reboot}`, `server.delete`, `permission.grant`. Эскалация компрометированного worker-PAT ограничена этим scope'ом. Гранты лендит миграция `43cf9cfef9e1_seed_worker_bot_entity_permissions.py` в server_service.

## Running locally (as part of the dev stack)

```bash
cd ..              # to dbos_server_service/
make up            # postgres + redis + auth + logging + server + worker
make logs          # tail worker logs
make sh-worker     # bash inside the running worker container
make test-worker   # run worker pytest suite (1486 tests)
make down          # stop the stack
```

## Architecture map

```
src/
  main.py                       # taskiq Broker + scheduler + startup/shutdown hooks
  core/
    config.py                   # Settings: DATABASE_URL, REDIS_URL, SERVER_SERVICE_URL, scheduler/shutdown/heartbeat thresholds
    constants.py                # TaskKind, TaskStatus enums
    exceptions.py               # CredentialFetchError, AuditEmitError
  db/{base,session,migrations}  # SQLAlchemy + Alembic (0001 tasks, 0002 audit_outbox, 0003 retry+heartbeats)
  models/
    task.py                     # Task ORM (scheduled_retry_at, worker_id)
    audit_outbox.py             # AuditOutbox ORM
    worker_heartbeat.py         # WorkerHeartbeat ORM
  repositories/
    task.py                     # CAS mark_running, mark_pending_for_retry, list_due_scheduled_retries, list_orphaned_running, delete_completed_older_than
    audit_outbox.py             # delete_published_older_than (retention cleanup)
    worker_heartbeat.py         # UPSERT, delete_stale_heartbeats
  clients/
    redfish.py                  # iDRAC/iLO Redfish (Systems/.../Actions/ComputerSystem.Reset, BootSourceOverride*)
    ipmitool.py                 # subprocess fallback для legacy BMC без Redfish
    ssh.py                      # asyncssh facade (chpasswd, inventory facts)
    __init__.py                 # get_bmc_client dispatcher (HEAD /redfish/v1/ probe)
  services/
    audit_client.py             # fail-loud POST /api/logging/v1/events
    audit_outbox_publisher.py   # background loop + SKIP LOCKED + circuit breaker + DLQ (3 reasons)
    server_service_client.py    # X-Target-Department-Id forward, internal credentials/rotate/callbacks
  tasks/
    _runner.py                  # task lifecycle wrapper (CAS, retry, audit, 2-session pattern)
    _runner_state.py            # RUNNING_TASKS set, get_worker_id()
    _bmc_helpers.py             # extract_bmc_host: strip scheme/port из endpoint_url для get_bmc_client
    _bmc_errors.py              # wrap_bmc_error → BMC_UNREACHABLE/BMC_AUTH_FAILED/BMC_REJECTED/BMC_TIMEOUT/BMC_ERROR
    power.py                    # power.{on,off,reboot,status}
    inventory.py                # inventory.{sync,probe} + callback inventory_received
    passwords.py                # account.rotate_password (real SSH), ipmi.rotate_password (real BMC)
    installed_packages.py       # installed_packages.list (live SSH dpkg-query/rpm -qa)
  # secrets.reencrypt_lazy зарегистрирован напрямую в main.py через broker.task — отдельного файла tasks/secrets.py пока нет
  utils/
    ids.py                      # tsk_* generator
    redaction.py                # 7 regex'ов для last_error / stdout
```

## Task handlers

| Task kind | Audit action | Состояние |
|---|---|---|
| `power.on` | `server.power_on` | real Redfish/ipmitool через `_get_bmc()` dispatcher (HEAD probe + fallback) |
| `power.off` | `server.power_off` | real Redfish/ipmitool — **hard ForceOff** (Redfish `ResetType: ForceOff` / ipmitool `chassis power off`), без graceful/ACPI shutdown |
| `power.reboot` | `server.power_reboot` | real Redfish/ipmitool |
| `power.status` | `server.power_status` | real Redfish/ipmitool |
| `inventory.sync` | `server.inventory_sync` | real SSH (`asyncssh`) → facts → callback `inventory_received` в server_service |
| `inventory.probe` | `server.inventory_probe` | real SSH probe (lightweight reachability check) |
| `account.rotate_password` | `server_account.password_rotate` | real SSH `chpasswd` + callback `credentials_rotated_callback` с зашифрованным ciphertext |
| `ipmi.rotate_password` | `ipmi_controller.password_rotate` | real: storage-first → BMC через Redfish с fallback на ipmitool + callback `ipmi_credentials_rotated_callback` |
| `installed_packages.list` | `installed_packages.list` | real SSH `dpkg-query` / `rpm -qa` через `asyncssh` |

Все handler'ы обёрнуты в `_runner.run_task(task_id, audit_action, audit_target_type, impl)`. 2-session pattern: mark_running CAS → impl → terminal status + audit outbox в одной транзакции.

**`_bmc_helpers.extract_bmc_host`** — общий хелпер для power/passwords handler'ов: режет `scheme://`, port и trailing slash из `endpoint_url` так, чтобы dispatcher не получил `https://https://...` при HEAD-probe. Раньше дублировался копипастой в `power.py` и `passwords.py`.

## Audit outbox — at-least-once

`_runner` пишет audit-row в `audit_outbox` table в той же сессии, что и terminal status (`succeeded`/`failed`). Background loop публикует:

- `SELECT … FOR UPDATE SKIP LOCKED` — concurrent publishers (multi-pod) не конфликтуют.
- `_BATCH_SIZE=50` rows per tick, `_POLL_INTERVAL_SECONDS=2.0`.
- **Circuit breaker**: N consecutive AuditEmitError → open на back-off (cap 300s).
- **DLQ (3 reasons)**: row помечается `published_at=now()` + ERROR в лог и больше не ретраится. Счётчик `audit_outbox_dead_total` (per-process monotonic) — stub под Prometheus.
  - `attempts_cap` — `attempts >= MAX_PUBLISH_ATTEMPTS` (default 50, env-overridable). Защита от зацикливания SKIP LOCKED-очереди на permanent transient ошибке.
  - `missing_action` — corrupted row без обязательного `action` — retry не починит, сразу в DLQ.
  - `permanent_4xx` — loging вернул 4xx, кроме `429/408` (rate-limit / timeout — retriable). 4xx означает malformed payload или revoked actor; счётчик breaker'а не трогается.

## Retry / durable scheduling

- `mark_running` — CAS `UPDATE tasks SET status='running', attempt=attempt+1 WHERE id=:id AND status='queued' RETURNING id`. При rejection — audit `duplicate_dispatch`, impl не вызывается. Защищает от повторного enqueue и race двух worker'ов.
- При failure `_runner` решает retry vs terminal по `attempt < max_attempts`:
  - retry → `mark_pending_for_retry` (status='queued', `scheduled_retry_at = now() + back-off`) + `_schedule_retry` (fire-and-forget `asyncio.sleep` + `broker.find_task.kicker().kiq`);
  - exhausted → `mark_failed`.
- **Durable**: `scheduled_retry_at` пишется в DB **до** sleep'а. При WORKER_STARTUP хук `_recover_scheduled_retries` SELECT'ит `scheduled_retry_at <= now()` через `FOR UPDATE SKIP LOCKED` и re-kick'ает — даже если worker умер во время back-off sleep'а, retry не теряется.
- Back-off: `min(10s * 2^(attempt-1), 300s)`. Для `max_attempts=3` это 10s → 20s.

## Graceful shutdown

При SIGTERM / WORKER_SHUTDOWN:

1. Останавливаем `audit_outbox_publisher` task (cancel + await).
2. Ждём до `WORKER_SHUTDOWN_TIMEOUT_SECONDS` (default 30s, под k8s `terminationGracePeriodSeconds`) пока `RUNNING_TASKS` set опустеет естественным образом.
3. Для оставшихся: CAS — если `attempt < max_attempts` → `mark_pending_for_retry` (новый worker подберёт через durable-recovery), иначе `mark_failed("worker_shutdown")` + audit-row.
4. Best-effort `flush_outbox` — audit о shutdown'е уходит до SIGKILL.

Без этого taskiq отменял coroutine'ы где-то в середине impl, и task в DB оставалась `status='running'` навсегда.

## Scheduler (periodic tasks)

`scheduler = TaskiqScheduler(broker, [LabelScheduleSource])` экспортирован на module-level. Запуск отдельным процессом:

```bash
taskiq scheduler src.main:scheduler
```

Все periodic-task'и регистрируются на broker'е **всегда** (нужны для `find_task` на worker-стороне), но `schedule` cron-label применяется только при `SCHEDULER_ENABLED=true`. В non-prod (default) можно kiq'нуть вручную из теста или ops-консоли.

| Task | Cron (UTC) | Назначение |
|---|---|---|
| `system.heartbeat` | `*/1 * * * *` | demo «alive» в stdout |
| `worker.heartbeat` | `*/1 * * * *` | UPSERT `(worker_id, now())` в `worker_heartbeats` |
| `tasks.sweep_orphaned` | `*/1 * * * *` | mark_failed `running` тасков, чей `worker_id` давно не heartbeat'ил |
| `worker.cleanup_stale_heartbeats` | `0 * * * *` | DROP heartbeat-row'ов старше `WORKER_HEARTBEAT_CLEANUP_THRESHOLD_SECONDS` (default 7d) |
| `tasks.cleanup_completed_old` | `0 0 * * *` (03:00 MSK) | DELETE SUCCEEDED/FAILED task'ов старше `TASKS_RETENTION_DAYS` (default 30d); bounded growth `tasks`. QUEUED/RUNNING не трогаем — это работа orphan-sweep'а. |
| `audit_outbox.cleanup_published_old` | `30 0 * * *` (03:30 MSK) | DELETE published outbox-row'ов (как delivered, так и DLQ-poisoned) старше `AUDIT_OUTBOX_RETENTION_DAYS` (default 90d). Сдвиг от `tasks.cleanup_completed_old` чтобы не пересекаться по DB-write нагрузке. |
| `secrets.reencrypt_lazy` | `*/5 * * * *` | bulk re-encrypt server_account / ipmi_controller паролей под актуальный `key_version` master-ключа — ходит в server_service `/secrets/migration_status` + `/secrets/reencrypt_batch`. Skip'ается, если `RUNNING_TASKS` непуст — приоритет ниже пользовательских handler'ов. |

Cron в taskiq читается в UTC; MSK-времена в комментариях для оператора. Расписания планируем по московскому времени (Europe/Moscow, UTC+3), а в БД и брокер всё уходит в UTC. Hardware-handlers (`power.*`, `ipmi.rotate_password`) используют BMC dispatcher из `tasks/_bmc_helpers.py`: HEAD-probe `/redfish/v1/` → Redfish-клиент, иначе fallback на `ipmitool` (`clients/ipmitool.py`).

## Running

```bash
# Worker
taskiq worker src.main:broker --workers 2

# Scheduler (optional, separate pod/process)
SCHEDULER_ENABLED=true taskiq scheduler src.main:scheduler
```

## Тесты

```bash
make test-worker
# 1486 passed, 27 test-файлов
```

## Конфигурация (env-vars)

### Базовые

| ENV | Default | Назначение |
|---|---|---|
| `APP_ENV` | `local` | `production` включает REDIS_URL password-validator, https-guard для outbound URL и обязательность `LOGGING_SERVICE_API_KEY` |
| `DATABASE_URL` | — | async DSN к `dev_server_worker` |
| `REDIS_URL` | `redis://redis:6379/0` | taskiq broker. В `production` обязан содержать password-сегмент (`redis://:<pwd>@host:port/db`) — иначе старт падает на validator'е |
| `SERVER_SERVICE_URL` | — | base URL server_service (internal credentials/rotate/callbacks). В `production`/`staging` обязан быть https (кроме localhost) |
| `AUTH_SERVICE_URL` | — | base URL auth_service для introspect собственного bot-токена |
| `LOGGING_SERVICE_URL` | — | base URL loging_service для audit-публикаций |
| `WORKER_BOT_TOKEN` | `""` | PAT worker_bot. Если пусто — entrypoint берёт из `/shared/.worker_pat` (его кладёт `make seed`) |
| `LOGGING_SERVICE_API_KEY` | `""` | shared SERVICE_API_KEY для `/api/logging/v1/events`. В `production` обязателен (иначе `audit_client.emit` тихо дропал бы события) |
| `HTTP_REQUEST_TIMEOUT_SECONDS` | `5.0` | httpx timeout для client-вызовов в server_service / loging_service |
| `WORKER_LOG_LEVEL` | `INFO` | python log level. `DEBUG` повышает шум, но `httpx/httpcore/hpack` принудительно понижены до WARNING (защита от утечки `Authorization` в логи) |

### Жизненный цикл и replica identity

| ENV | Default | Назначение |
|---|---|---|
| `WORKER_SHUTDOWN_TIMEOUT_SECONDS` | `30.0` | grace при SIGTERM/`WORKER_SHUTDOWN`. После таймаута survivors → `mark_pending_for_retry` (если attempts < max) или `mark_failed("worker_shutdown")` |
| `SCHEDULER_ENABLED` | `false` | при `true` periodic'и регистрируются с cron-label; при `false` task'и есть на broker'е (нужны для `find_task`), но не дёргаются автоматически |
| `WORKER_ID` | hostname-pid | стабильный идентификатор replica для heartbeat и orphan-sweep'а |
| `WORKER_ORPHAN_THRESHOLD_SECONDS` | `1800.0` | мин. длительность `status='running'` чтобы task считался orphan-кандидатом (потолок realistic impl-runtime) |
| `WORKER_HEARTBEAT_STALE_SECONDS` | `300.0` | через сколько без heartbeat'а worker_id считается мёртвым; должно быть заметно больше cron-периода (60s) |
| `WORKER_HEARTBEAT_CLEANUP_THRESHOLD_SECONDS` | `604800.0` (7d) | DROP старых row'ов в `worker_heartbeats` hourly cleanup'ом |

### Retention (bounded growth) и audit outbox

| ENV | Default | Назначение |
|---|---|---|
| `TASKS_RETENTION_DAYS` | `30` | сколько дней хранить SUCCEEDED/FAILED task'и до daily DELETE. QUEUED/RUNNING никогда не трогаем — за них отвечает orphan-sweep |
| `AUDIT_OUTBOX_RETENTION_DAYS` | `90` | сколько дней хранить published outbox-row'ы (и delivered, и DLQ-poisoned) до daily DELETE. Unpublished (in-flight) не трогаем |
| `MAX_PUBLISH_ATTEMPTS` | `50` | cap по attempts в publisher loop'е. При превышении row уходит в DLQ через `_send_to_dlq` с `reason="attempts_cap"`; счётчик `audit_outbox_dead_total` тикает (stub под Prometheus) |

### BMC / Redfish / PXE

| ENV | Default | Назначение |
|---|---|---|
| `IPMI_USER_ID` | `2` | дефолтный Redfish account slot (`/Managers/<m>/Accounts/<n>`). Dell iDRAC root=2, HPE iLO=1, Supermicro=3. Per-host override через payload |
| `REDFISH_VERIFY_TLS` | `false` | iDRAC ships self-signed cert — `true` только когда BMC получили cert от внутреннего CA |
| `REDFISH_TIMEOUT_SECONDS` | `30.0` | per-request timeout для Redfish-вызовов |
| `PXE_HOST` | `""` | hostname/IP PXE-TFTP сервера. Пусто → `reinstall.start` падает с `PXE_NOT_CONFIGURED` |
| `PXE_SSH_USERNAME` | `""` | SSH-user для PXE-host'а (service-account, не root) |
| `PXE_SSH_PASSWORD` | `""` | SSH-пароль PXE-host'а (в проде через k8s Secret) |
| `PXE_TFTP_ROOT` | `/srv/tftp` | абсолютный путь TFTP root на PXE-host'е |
| `PXE_STRICT_HOST_KEY_CHECKING` | `false` | strict host-key check для SSH-коннектов к PXE-host'у. В prod ставить `true` и mount'ить `known_hosts`; в dev/test — `false` |
| `REINSTALL_TIMEOUT_SECONDS` | `1800.0` | максимум сколько `reinstall.start` ждёт pipeline до `mark_failed` |
| `REINSTALL_POLL_INTERVAL_SECONDS` | `30.0` | sleep между health-check probe'ами в reinstall-wait цикле |
| `REINSTALL_DEFAULT_OS_IMAGE_URL` | `""` | fallback `os_image` URL, если в payload не пришёл |

### Master-key re-encryption (background)

| ENV | Default | Назначение |
|---|---|---|
| `SECRETS_REENCRYPT_ENABLED` | `true` | включает periodic `secrets.reencrypt_lazy` (5-минутный тик). В тестах/dev стеках, где `/internal/secrets/*` недостижим, выключить |
| `SECRETS_REENCRYPT_INTERVAL_SECONDS` | `300.0` | cooldown между тиками (≥1.0). Не путать с cron-расписанием самого periodic'а — это hot-loop guard внутри handler'а |
| `SECRETS_REENCRYPT_BATCH_SIZE` | `100` | размер `reencrypt_batch` запроса в server_service. Worker не делает несколько батчей за тик — high-priority задачи должны успевать прорваться между ними |
