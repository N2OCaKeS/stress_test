"""Настройки приложения."""

import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Дефолт cron'а авто-inventory: 04:00 MSK = 01:00 UTC (taskiq читает cron в UTC).
# Ночной низкий traffic, со сдвигом от housekeeping-cleanup'ов (00:00-00:30 UTC).
_AUTO_INVENTORY_CRON_DEFAULT = "0 1 * * *"

# Дефолт cron'а частого power-sweep'а: каждые 5 минут. Проба ping/ssh/ipmi
# read-only и лёгкая, но держит доступность и питание всех серверов актуальными
# (иначе значения «протухают» между суточными inventory-прогонами).
_POWER_SWEEP_CRON_DEFAULT = "*/5 * * * *"

# Дефолт cron'а частого статус-sweep'а ВМ: каждые 5 минут, как у серверного
# power-sweep'а. Проба domstate/ping/ssh read-only и лёгкая, держит питание и
# доступность гостей актуальными между lifecycle-операциями.
_VM_STATUS_CRON_DEFAULT = "*/5 * * * *"

# Дефолт cron'а reconcile'а упавших vm.create: каждые 3 минуты. Прогон дешёвый
# (SELECT ВМ в busy_state=creating + статус их задач), но удаляет ВМ, чью
# create-задачу воркер терминально завалил — реагировать хочется быстро.
_VM_CREATE_RECONCILE_CRON_DEFAULT = "*/3 * * * *"


# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password
# (`redis://redis:6379/0`) и без user:pass-сегмента вообще — не матчит.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Hostnames, где plain `http://` допустим даже в production/staging —
# devcontainer / sidecar / on-host debug-сценарии, где TLS терминируется
# на той же машине и MITM-модель другая. Симметрично
# `loging_service/src/core/config.py:_LOCAL_HOSTS`.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

def _is_intracluster_host(host: str) -> bool:
    """Cluster-local DNS-имя (short name без точки или .svc/.cluster.local).
    Plain http между pod'ами равноценен self-loop'у на localhost: внутрикластерный
    traffic закрыт NetworkPolicy default-deny + namespace boundary. External
    FQDN'ы (example.com) этот фильтр не пропускает — там https:// остаётся обязательным.
    """
    return (
        host in _LOCAL_HOSTS
        or "." not in host
        or host.endswith((".svc", ".svc.cluster.local", ".cluster.local"))
    )


# Окружения, в которых требуется https:// для исходящих HTTP-вызовов.
_HTTPS_REQUIRED_ENVS: frozenset[str] = frozenset({"production", "staging"})

# Окружения, в которых обязателен непустой `LOGGING_SERVICE_API_KEY`. Пустой
# ключ заставляет `audit_client.emit` тихо дропать события — для prod и
# staging (где стоит реальный SIEM-приёмник) это материальная дыра. local/test
# остаются без проверки: dev-стек и conftest поднимаются без секрета.
_AUDIT_KEY_REQUIRED_ENVS: frozenset[str] = frozenset({"production", "staging"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(
        default="local",
        description=(
            "Deployment environment label: local|dev|test|staging|production. "
            "В 'production' включается строгий validator: REDIS_URL обязан содержать "
            "password (защита taskiq queue от анонимного RPUSH из соседних подов). "
            "В 'production'/'staging' enforce'ится https:// для outbound URL. "
            "Тип — str, не Literal: case-insensitive (`.lower()` в guards), и тесты "
            "явно проверяют `APP_ENV=PRODUCTION`. См. test_config_redis_auth.py."
        ),
    )
    database_url: str = Field(
        ...,
        description="PostgreSQL async DSN for the worker's own DB (dev_server_worker)",
    )
    server_service_database_url: str = Field(
        default="",
        description=(
            "PostgreSQL async DSN for the server_service DB (dev_server_service). "
            "Используется dispatch_outbox publisher'ом — он читает строки outbox'а "
            "из server_service-БД (миграция таблицы там же) и публикует задачи в "
            "taskiq-broker. Пустой → publisher молча скипает тик (dev/test/local)."
        ),
    )
    db_pool_size: int = Field(
        default=5,
        ge=1,
        alias="DB_POOL_SIZE",
        description=(
            "SQLAlchemy pool_size для воркер-engine. Дефолт 5 рассчитан на "
            "1-2 active handler'а + heartbeat/sweep/outbox-publisher. При "
            "более широкой concurrency (taskiq --workers >2, выше "
            "WORKER_HANDLER_CONCURRENCY) — поднимать через env."
        ),
    )
    worker_handler_concurrency: int = Field(
        default=4,
        ge=1,
        le=64,
        alias="WORKER_HANDLER_CONCURRENCY",
        description=(
            "Maximum number of concurrent in-flight task handlers per worker "
            "process. Enforced by asyncio.Semaphore вокруг impl-вызова в "
            "`_runner.run_task`. taskiq `--workers=N` поднимает N процессов "
            "независимо; этот лимит — потолок ВНУТРИ процесса (по умолчанию 4). "
            "Tune up для I/O-bound нагрузок (SSH/IPMI ожидают сеть); tune down "
            "если DB_POOL_SIZE/audit/server_service пулов не хватает на бурст. "
            "Hard cap 64 — за ним пулы httpx/redis всё равно начнут давиться."
        ),
    )
    db_max_overflow: int = Field(
        default=10,
        ge=0,
        alias="DB_MAX_OVERFLOW",
        description=(
            "SQLAlchemy max_overflow поверх pool_size. Burst-кап на короткие "
            "пики (fan-out reconcile, drain). Должен быть >= ожидаемого burst'а "
            "минус pool_size."
        ),
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description=(
            "Redis URL for the taskiq broker. В продакшене обязательно с password, "
            "формат `redis://:<password>@host:port/db`. Иначе любой контейнер "
            "в той же сети может RPUSH-нуть payload в очередь воркера."
        ),
    )
    taskiq_queue_name: str = Field(
        default="taskiq",
        description=(
            "Имя normal-очереди taskiq (Redis-список). Воркер дренирует её "
            "после high-priority-очереди."
        ),
    )
    taskiq_high_priority_queue_name: str = Field(
        default="taskiq_high",
        description=(
            "Имя high-priority-очереди taskiq (отдельный Redis-список). "
            "dispatch_outbox publisher кладёт сюда задачи с priority >= "
            "high-порога; воркер дренирует её ПЕРЕД normal-очередью через "
            "`BRPOP high normal` — один атомарный блокирующий pop, который "
            "забирает high первым, если в нём что-то есть."
        ),
    )
    redis_stash_encryption_key: str = Field(
        default="",
        description=(
            "Симметричный master-ключ для decrypt'а Redis-stash'а кред, "
            "положенного server_service'ом (provision/prepare dispatch). "
            "Mount'ится из того же Secret'а, что и у server_service'а — "
            "обязан совпадать байт-в-байт, иначе worker увидит "
            "STASH_DECRYPT_FAILED и task FAILED. В production проверяется "
            "только когда фактически нужен (handler'у пришёл шифр-токен), "
            "пустота на старте не валит worker — некоторые дев-сценарии "
            "ходят без stash'ей. Генерить: `openssl rand -base64 32`."
        ),
    )
    redis_stash_encryption_key_version: int = Field(
        default=1,
        ge=1,
        description=(
            "Активная версия `REDIS_STASH_ENCRYPTION_KEY` на worker-стороне. "
            "Симметрично server_service'у. Старые версии читаются по "
            "`REDIS_STASH_ENCRYPTION_KEY__v<N>` env — нужно при ротации, "
            "чтобы stash'и в полёте от предыдущей версии остались читаемыми."
        ),
    )
    hkdf_salt_hex: str = Field(
        default="",
        description=(
            "HKDF-salt в hex для деривации stash-AES-ключа. ОБЯЗАН совпадать "
            "с `HKDF_SALT_HEX` у server_service'а, иначе KDF-output разъедется "
            "и worker не decrypt'нёт ни один stash. Минимум 32 hex-символа "
            "(16 байт). Пустая строка — fallback на dev-константу (только для "
            "dev/test/local; prod-guard у server_service'а отбивает пустоту)."
        ),
    )
    server_service_url: str = Field(
        ...,
        description="Base URL of server_service for internal credential calls",
    )
    auth_service_url: str = Field(
        ...,
        description="Base URL of auth_service (introspect, for worker's own bot token)",
    )
    logging_service_url: str = Field(
        ...,
        description="Base URL of logging_service for audit publication",
    )
    worker_bot_token: str = Field(
        default="",
        description="Bearer token (PAT) for worker → server_service calls",
    )
    logging_service_api_key: str = Field(
        default="",
        description="Shared SERVICE_API_KEY used by loging_service /events ingest",
    )
    http_request_timeout_seconds: float = Field(
        default=5.0,
        description=(
            "Legacy общий HTTP timeout. Оставлен как fallback / для тестов; "
            "production-path развёл audit и server_service через "
            "`audit_request_timeout_seconds` / `server_service_request_timeout_seconds`."
        ),
    )
    # Канал worker → loging_service: emit'ы аудита короткие, low-latency
    # ожидается от ingest'а. 5s достаточно на TLS handshake + INSERT в logs.
    audit_request_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "HTTP timeout для emit'ов в loging_service /events. Короткий — "
            "ingest должен отвечать быстро; на медленных проходах rule_engine "
            "5s включит outbox retry, что предпочтительнее зависшего worker'а."
        ),
    )
    # Канал worker → server_service: internal-callback'и тащат крипто (decrypt
    # /encrypt /UPDATE row под master-key) и могут упереться в slow PostgreSQL,
    # легко выйдя за 5s. 15s — компромисс между «не зависать» и «не ловить
    # ложный SERVER_SERVICE_UNREACHABLE».
    server_service_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description=(
            "HTTP timeout для internal-callback'ов в server_service "
            "(fetch_*/submit_*). Длиннее audit-timeout'а: криптооперации "
            "сервера могут тянуться 50-200ms на row, callback'и упираются "
            "в slow PG."
        ),
    )
    worker_log_level: str = Field(default="INFO", description="Python log level")

    # ── Pooled HTTP clients ──────────────────────────────────────────────
    # Долгоживущие httpx.AsyncClient'ы под два горячих исходящих канала:
    # audit-emit в loging_service и callback'и в server_service. До этой
    # настройки каждый emit/callback создавал свежий `httpx.AsyncClient` с
    # последующим TCP/TLS handshake — амплификация file-descriptor'ов и
    # лишняя латентность при burst'е задач.
    #
    # `max_connections` — общий лимит одновременных соединений в пуле;
    # `max_keepalive_connections` — сколько держать idle для reuse.
    # Дефолты подобраны под скромный worker-RPS: audit-эмит идёт
    # пачками от outbox-publisher'а, callback'и в server_service —
    # 1-2 на task. При desync конкретного стенда поднимать через env.
    audit_pool_max_connections: int = Field(
        default=20,
        ge=1,
        description=(
            "Total concurrent connections in the pooled httpx client to "
            "loging_service. Tune up if audit-outbox publisher batches "
            "are large and observe queueing on emit()."
        ),
    )
    audit_pool_max_keepalive_connections: int = Field(
        default=10,
        ge=1,
        description=(
            "Idle keepalive connections to keep open for reuse against "
            "loging_service. Should be <= audit_pool_max_connections."
        ),
    )
    server_service_pool_max_connections: int = Field(
        default=20,
        ge=1,
        description=(
            "Total concurrent connections in the pooled httpx client to "
            "server_service. Covers fetch_*/submit_* callbacks from every "
            "task handler."
        ),
    )
    server_service_pool_max_keepalive_connections: int = Field(
        default=10,
        ge=1,
        description=(
            "Idle keepalive connections to keep open for reuse against "
            "server_service. Should be <= server_service_pool_max_connections."
        ),
    )
    # ── BMC HTTP pool (Redfish + scheme probe) ───────────────────────────
    # Один пул соединений для всех BMC-вызовов: probe HEAD `/redfish/v1/`
    # (3 клиента по (scheme, verify)) и per-host RedfishClient через shared
    # httpx-transport (2 транспорта по verify). До этой настройки каждый
    # power-action / rotate-password создавал свежий httpx.AsyncClient с
    # TCP/TLS handshake на каждый roundtrip — амплификация FD'ов при burst'е
    # параллельных задач на разные BMC. Лимиты — суммарно на все BMC.
    bmc_pool_max_connections: int = Field(
        default=20,
        ge=1,
        description=(
            "Total concurrent connections in the pooled BMC httpx clients "
            "(Redfish transport + scheme probe). Covers all BMC hosts "
            "addressed by a single worker; tune up if a stand has many "
            "BMCs handled in parallel."
        ),
    )
    bmc_pool_max_keepalive_connections: int = Field(
        default=10,
        ge=1,
        description=(
            "Idle keepalive connections to keep open for reuse against "
            "BMCs. Should be <= bmc_pool_max_connections."
        ),
    )
    bmc_probe_timeout_seconds: float = Field(
        default=1.5,
        ge=0.1,
        description=(
            "Timeout for HEAD `/redfish/v1/` probe per scheme step in the "
            "cascade. Kept short so ipmitool fallback doesn't wait long "
            "on dead BMC. Default 1.5s."
        ),
    )

    # ── Graceful shutdown ────────────────────────────────────────────────
    # Максимум сколько ждать running task'ам завершиться при SIGTERM /
    # WORKER_SHUTDOWN, прежде чем mark_pending_for_retry (если max_attempts
    # позволяет) либо mark_failed("worker_shutdown"). 30s — дефолт k8s
    # `terminationGracePeriodSeconds` (typically 30). Если k8s даст SIGKILL
    # раньше — running task'и останутся в DB и подхватятся watchdog'ом /
    # operator-cron'ом, это deliberate (см. `_drain_running_tasks` docstring).
    worker_shutdown_timeout_seconds: float = Field(
        default=30.0,
        description=(
            "Max seconds the worker waits for running tasks to finish on "
            "SIGTERM. After timeout — surviving tasks are marked "
            "pending-for-retry (if attempts left) or failed (terminal)."
        ),
    )

    # Cap на recovery-проход `_recover_due_scheduled_retries_once`. Дефолт
    # 200/тик хватает на штатные сценарии. После длительного downtime'а с
    # тысячами потерянных retry-row'ов 200/min = неприемлемо медленный
    # разгребатель — поднимать через env только под incident-recovery,
    # потом возвращать в default.
    worker_retry_recovery_max_per_tick: int = Field(
        default=200,
        ge=1,
        description=(
            "Hard cap для одного прохода recovery'я scheduled_retry'ев "
            "(startup-hook и periodic-cron оба зовут `_recover_due_..._once`). "
            "Поднимать только во время incident-recovery (>1000 потерянных "
            "row'ов после длительного downtime'а)."
        ),
    )

    # ── Scheduler (taskiq.scheduler) ─────────────────────────────────────
    # Скелет для periodic tasks. Сам scheduler — отдельный процесс
    # (`taskiq scheduler src.main:scheduler`); broker внутри worker'а его
    # просто принимает через Redis. SCHEDULER_ENABLED управляет регистрацией
    # periodic-таски `worker_heartbeat` (60s) — она пишет `last_heartbeat_at`
    # в DB и используется как операционный сигнал «worker жив». В non-prod
    # (`local`/`dev`/`test`) дефолт отключён, чтобы CI/dev-стек поднимался
    # без лишних запросов.
    scheduler_enabled: bool = Field(
        default=False,
        description=(
            "Enable the taskiq.scheduler skeleton. In production set "
            "`SCHEDULER_ENABLED=true` and run `taskiq scheduler "
            "src.main:scheduler` as a separate pod/sidecar."
        ),
    )

    # ── Плановый авто-inventory + power подготовленных серверов ──────────
    # Периодик `auto_inventory.sweep` по cron'у дёргает server_service
    # internal-эндпоинт /servers/auto-inventory-sweep — тот ставит inventory.sync
    # + power.status на все managed-серверы. Воркер лишь даёт расписание,
    # фан-аут и throttle живут на стороне server_service. Регистрируется только
    # при `scheduler_enabled AND auto_inventory_enabled`.
    auto_inventory_enabled: bool = Field(
        default=True,
        description=(
            "Enable the periodic auto-inventory/power sweep task "
            "(`auto_inventory.sweep`). Requires SCHEDULER_ENABLED as well."
        ),
    )
    auto_inventory_cron: str = Field(
        default=_AUTO_INVENTORY_CRON_DEFAULT,
        description=(
            "Cron for the periodic auto-inventory/power sweep (UTC, taskiq). "
            "Default `0 1 * * *` = 04:00 MSK, once a day."
        ),
    )

    # ── Частый power-sweep (ping/ssh/ipmi по всем серверам) ──────────────
    # Периодик `power.sweep` по частому cron'у дёргает server_service
    # internal-эндпоинт /servers/power-sweep — тот ставит ТОЛЬКО power.status на
    # ВСЕ не-списанные серверы (без inventory.sync, без фильтра is_managed).
    # Держит доступность (ping/ssh) и питание (ipmi) актуальными для каждого
    # сервера. Регистрируется при `scheduler_enabled AND power_sweep_enabled`.
    power_sweep_enabled: bool = Field(
        default=False,
        description=(
            "Enable the frequent power/reachability sweep task (`power.sweep`) "
            "that dispatches power.status for every server. Superseded by the "
            "in-worker probe loops (services/probe_loop.py, PROBE_LOOP_*) — off "
            "by default so it no longer spams task rows. The task stays "
            "registered for manual kiq. Requires SCHEDULER_ENABLED to schedule."
        ),
    )
    power_sweep_cron: str = Field(
        default=_POWER_SWEEP_CRON_DEFAULT,
        description=(
            "Cron for the frequent power/reachability sweep (UTC, taskiq). "
            "Default `*/5 * * * *` = every 5 minutes."
        ),
    )

    # ── Частый статус-sweep ВМ (domstate/ping/ssh по всем ВМ) ────────────
    # Периодик `vms.status_sweep` по частому cron'у дёргает server_service
    # internal-эндпоинт /vms/status-sweep — тот ставит `vm.status` на ВСЕ
    # активные ВМ (питание domstate + ping/ssh гостя). Зеркало power-sweep'а
    # серверов. Регистрируется при `scheduler_enabled AND vm_status_sweep_enabled`.
    vm_status_sweep_enabled: bool = Field(
        default=False,
        description=(
            "Enable the frequent VM status sweep task (`vms.status_sweep`) that "
            "dispatches vm.status for every VM. Superseded by the in-worker probe "
            "loops (services/probe_loop.py, PROBE_LOOP_*) — off by default so it "
            "no longer spams task rows. The task stays registered for manual kiq. "
            "Requires SCHEDULER_ENABLED to schedule."
        ),
    )
    vm_status_cron: str = Field(
        default=_VM_STATUS_CRON_DEFAULT,
        description=(
            "Cron for the frequent VM status sweep (UTC, taskiq). "
            "Default `*/5 * * * *` = every 5 minutes."
        ),
    )

    # Reconcile упавших vm.create: worker даёт только расписание, фан-аут
    # (ВМ в busy_state=creating + статус их задач + удаление провалившихся)
    # выполняет server_service. Регистрируется при
    # `scheduler_enabled AND vm_create_reconcile_enabled`.
    vm_create_reconcile_enabled: bool = Field(
        default=True,
        description=(
            "Enable the periodic reconciler (`vms.reconcile_failed_creates`) "
            "that removes VMs whose vm.create task terminally failed. Requires "
            "SCHEDULER_ENABLED as well."
        ),
    )
    vm_create_reconcile_cron: str = Field(
        default=_VM_CREATE_RECONCILE_CRON_DEFAULT,
        description=(
            "Cron for the failed-vm.create reconciler (UTC, taskiq). "
            "Default `*/3 * * * *` = every 3 minutes."
        ),
    )

    # ── Worker identity / heartbeat / orphan sweep ──────────────────────
    # `worker_id` — стабильный идентификатор replica. В k8s достаточно
    # hostname (== pod name); для bare-metal или dev — явно задаваемое
    # значение. Пустая строка → runtime подберёт `socket.gethostname()-pid`
    # (см. `src/main.py::_resolve_worker_id`).
    worker_id: str = Field(
        default="",
        description=(
            "Stable identifier of this worker replica. Empty → derived "
            "from hostname-pid at startup. Written to tasks.worker_id and "
            "worker_heartbeats.worker_id."
        ),
    )
    # Сколько task'е разрешено быть `status='running'` прежде чем sweep
    # считает её orphan-кандидатом. Дефолт 180s — был 1800s, но это давало
    # ~35 мин окно от crash'а до finalize'а; уменьшили чтобы pod-loss
    # recovery укладывался в ~3 мин. Trade-off: handler, который штатно
    # выполняется дольше threshold'а, попадёт в sweep-кандидаты — но
    # heartbeat-фильтр (worker_id живой) такие легитимные case'ы пропускает.
    # Override через SWEEP_ORPHAN_THRESHOLD_SECONDS для smoke-тестов либо
    # если в стенде есть штатные многоминутные SSH ops без heartbeat.
    worker_orphan_threshold_seconds: float = Field(
        default=180.0,
        alias="SWEEP_ORPHAN_THRESHOLD_SECONDS",
        description=(
            "Min seconds a task must stay in 'running' status before the "
            "orphan sweep considers it. Sweep also requires the task's "
            "worker_id NOT to be among recently-heartbeating workers. "
            "Default lowered from 1800s to 180s for faster pod-loss recovery."
        ),
    )
    # Сколько секунд отсутствия heartbeat'а worker_id считается «упал».
    # Per-worker heartbeat теперь фоновый loop (services/heartbeat_loop.py),
    # бьётся каждые ~stale/3 независимо от handler-слотов, поэтому насыщенный
    # воркер под нагрузкой не протухает. Порог 120s даёт запас (loop ~40s → 2
    # пропущенных тика переживаются) и recovery реально мёртвого пода ~2 мин.
    worker_heartbeat_stale_seconds: float = Field(
        default=120.0,
        alias="SWEEP_HEARTBEAT_TIMEOUT_SECONDS",
        description=(
            "After this many seconds since last_heartbeat_at, a worker_id "
            "is considered inactive; tasks owned by it past the orphan "
            "threshold get marked failed by the sweep. Default lowered "
            "from 300s to 60s — heartbeat tick is 60s, поэтому одна "
            "пропущенная минута + jitter = inactive."
        ),
    )
    # Cleanup-порог для `worker_heartbeats`. Каждый pod-рестарт даёт новый
    # worker_id (k8s pod-suffix меняется), старый row остаётся forever.
    # Periodic `worker.cleanup_stale_heartbeats` дропает row'ы старше
    # этого порога — bounded growth таблицы. 7 дней = достаточно, чтобы
    # operator успел заметить «replica X не возвращается» по orphan-
    # sweep'у, и при этом не дать таблице расти бесконтрольно.
    worker_heartbeat_cleanup_threshold_seconds: float = Field(
        default=7 * 86400.0,
        description=(
            "Heartbeat rows older than this are dropped by the hourly "
            "cleanup task. Should be much larger than "
            "worker_heartbeat_stale_seconds — once a worker_id is "
            "considered inactive, orphan-sweep finalises its tasks; we "
            "keep the row a bit longer for forensics, then prune."
        ),
    )

    # ── Redfish / BMC client ─────────────────────────────────────────────
    # Slot user'а на iDRAC, к которому относятся rotate-операции пароля.
    # У Dell out-of-box `root` сидит в slot=2; у других вендоров (HPE,
    # Supermicro) может быть 1 или 3. Конкретные стенды задают override
    # через env. Когда credentials в server_service вырастут до
    # per-controller `user_id` — параметр уйдёт в payload, env-default
    # останется fallback'ом.
    ipmi_user_id: int = Field(
        default=2,
        description=(
            "Default Redfish account slot (`/Managers/<m>/Accounts/<n>`) "
            "to rotate password on. Dell iDRAC root = 2, HPE iLO root = 1, "
            "Supermicro = 3. Override per-host через payload."
        ),
    )
    # iDRAC поставляется с self-signed сертификатом — `verify_tls=False`
    # это стандарт по умолчанию. Переключать в `true` только когда в
    # стенде BMC получил cert от внутреннего CA, иначе любой запрос упадёт.
    redfish_verify_tls: bool = Field(
        default=False,
        description=(
            "Verify BMC TLS certificate. False default — iDRAC ships with "
            "self-signed cert. Set true only if BMCs are provisioned from "
            "an internal CA."
        ),
    )
    # Длинный таймаут на одиночный Redfish-вызов. Reset-actions BMC
    # обычно отвечают ≤2s, но virtual-media insert может тянуть ISO
    # минутами через httpd. 30s — компромисс: достаточно для обычных
    # операций, не блокирует worker'а навсегда при висящем BMC.
    redfish_timeout_seconds: float = Field(
        default=30.0,
        description=(
            "Per-request timeout for Redfish HTTP calls (connect + read). "
            "BMC reset typically <2s; virtual media operations can take "
            "longer. Worker retry/back-off handles transient failures. "
            "30s — compromise между fast verify-after-rotate (где overkill) и "
            "slow virtual-media insert (где мало). Per-operation override "
            "не реализован — owner trade-off; если станет тесно — разносить "
            "по action'у через payload."
        ),
    )

    # ── power.status reachability fallback ───────────────────────────────
    # Когда BMC не отвечает (недоступен / breaker open / неопределённое
    # состояние), power.status вместо немедленного fail'а пробует сетевую
    # достижимость самого сервера: ICMP-ping и TCP-коннект на SSH-порт. Если
    # бокс отвечает — состояние считается `on` (источник `ping`/`ssh`), иначе
    # остаётся `unknown`. Сетевая недоступность НЕ трактуется как `off`.
    power_reachability_fallback_enabled: bool = Field(
        default=True,
        description=(
            "Enable ping/SSH-port reachability fallback in power.status when "
            "the BMC probe fails to return a definite on/off state."
        ),
    )
    power_reachability_ping_timeout_seconds: float = Field(
        default=2.0, gt=0,
        description="Timeout for a single ICMP echo in the power.status fallback.",
    )
    power_reachability_tcp_timeout_seconds: float = Field(
        default=2.0, gt=0,
        description=(
            "Timeout for the SSH-port TCP connect in the power.status fallback."
        ),
    )
    power_reachability_ssh_port: int = Field(
        default=22, ge=1, le=65535,
        description=(
            "Default SSH port probed by the power.status reachability fallback "
            "when the dispatch payload carries no explicit ssh_port."
        ),
    )

    # ── Фоновые probe-циклы воркера (reachability + power) ────────────────
    # Заменяют частые sweep'ы power.sweep/vms.status_sweep: воркер держит два
    # фоновых asyncio-loop'а (services/probe_loop.py), тянет цели через
    # /internal/probe-targets и снимает ping/ssh/ipmi/domstate сам, не плодя
    # task-row'ы. Параллелизм внутри одного тика ограничен ВЫДЕЛЕННЫМ семафором
    # (не taskiq handler-слотами), чтобы шторм целей не забивал event-loop.
    probe_loop_concurrency: int = Field(
        default=8, ge=1, le=256,
        description=(
            "Max concurrent probes within one reachability/power loop tick "
            "(dedicated asyncio.Semaphore, independent of the taskiq handler "
            "pool). Intervals and on/off come from /internal/settings/probes, "
            "re-read fresh each cycle."
        ),
    )

    # ── BMC circuit breaker ──────────────────────────────────────────────
    # Per-host shared breaker (Redis-backed; см. services/bmc_circuit_breaker.py).
    # Многореплика worker'а: каждая реплика видит общий счётчик failure'ов и
    # общий open_until. Без shared state одна реплика отбивала бы запросы,
    # а вторая продолжала бы насиловать контроллер.
    bmc_breaker_failure_threshold: int = Field(
        default=5, ge=1,
        description=(
            "Сколько failure'ов на один host в окне window_seconds должно "
            "случиться, чтобы breaker перешёл из closed в open. По умолчанию 5."
        ),
    )
    bmc_breaker_window_seconds: int = Field(
        default=60, ge=1,
        description=(
            "Размер rolling-окна для счётчика failure'ов (TTL ключа "
            "cb:bmc:<host>:failures). 60s покрывает типичную серию retry'ев "
            "одной задачи."
        ),
    )
    bmc_breaker_cooldown_seconds: int = Field(
        default=30, ge=1,
        description=(
            "На сколько секунд open breaker отбивает запросы до перехода в "
            "half-open. После cooldown первый запрос проходит как пробный."
        ),
    )

    # ── Audit-publisher circuit breaker ──────────────────────────────────
    # Shared breaker для канала worker → loging_service /events (см.
    # services/audit_publisher_breaker.py). Per-process breaker в
    # audit_outbox_publisher остаётся — он защищает loop-level back-off на
    # длительные аварии; этот guard режет per-row HTTP-roundtrip'ы между
    # репликами, чтобы при N pod'ах в кластере один и тот же лежащий
    # loging_service не получал N × threshold лишних запросов.
    audit_publisher_breaker_failure_threshold: int = Field(
        default=5, ge=1,
        description=(
            "Сколько failure'ов POST'а в loging_service за окно "
            "window_seconds должно случиться, чтобы breaker замкнулся."
        ),
    )
    audit_publisher_breaker_window_seconds: int = Field(
        default=60, ge=1,
        description=(
            "Rolling-окно счётчика failures для audit-publisher breaker'а. "
            "TTL ключа cb:audit_publisher:failures."
        ),
    )
    audit_publisher_breaker_cooldown_seconds: int = Field(
        default=30, ge=1,
        description=(
            "На сколько секунд breaker отбивает POST'ы до перехода в "
            "half-open. После cooldown один POST проходит как пробный."
        ),
    )

    # ── installed_packages audit-detail masking ──────────────────────────
    # `installed_packages.list` принимает glob-pattern (`linux-image*`,
    # `openssl*`, …). По себе pattern — операционный параметр, но в audit-
    # details он намекает, какие CVE-релевантные пакеты оператор пробовал
    # инспектировать. Дефолт — масковать (pattern в audit не уходит, только
    # server_id/count/package_manager). Включается осознанно, когда нужно
    # сопоставить audit-row с конкретным запросом из server_service.
    audit_installed_packages_pattern_debug: bool = Field(
        default=False,
        description=(
            "When False (default), `installed_packages.list` strips `pattern` "
            "from the audit whitelist — only server_id/count/package_manager "
            "appear in audit-details. Set True to include `pattern` for "
            "correlation with the server_service request log; trade-off is "
            "CVE-recon visibility leaks."
        ),
    )

    # ── Audit-outbox publisher cap ───────────────────────────────────────
    # Soft-cap по `attempts` для одной outbox-row. После него publisher
    # помечает row как DLQ (`published_at=now()`, `last_error="[DLQ:attempts_cap]"`)
    # — событие потеряно, но row перестаёт засорять SKIP LOCKED-выборку.
    # Защита от permanent-422: без cap'а ретрай вечный, attempts может
    # перевалить за 2^31.
    max_publish_attempts: int = Field(
        default=50,
        ge=1,
        alias="MAX_PUBLISH_ATTEMPTS",
        description=(
            "Soft-cap на attempts в audit-outbox publisher. По достижении "
            "row уходит в DLQ через `_send_to_dlq(reason='attempts_cap')`. "
            "Override для операционных тестов и форс-дренажа."
        ),
    )

    # Параметры audit-outbox publisher loop'а. Symmetрично
    # dispatch-outbox (`dispatch_outbox_*`), чтобы оператор мог тюнить оба
    # publisher'а одинаково. Дефолты — те же, что были раньше как
    # module-private константы в `services/audit_outbox_publisher.py`.
    audit_outbox_batch_size: int = Field(
        default=5,
        ge=1,
        alias="AUDIT_OUTBOX_BATCH_SIZE",
        description=(
            "Сколько строк за один проход. Маленький batch ограничивает "
            "blast-radius медленных HTTP-вызовов в loging_service (весь "
            "batch держится FOR UPDATE SKIP LOCKED до commit'а); при "
            "batch=50 один медленный emit тормозит остальные 49."
        ),
    )
    audit_outbox_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0.0,
        alias="AUDIT_OUTBOX_POLL_INTERVAL_SECONDS",
        description=(
            "Пауза между проходами фонового publisher loop'а. Меньше — "
            "ниже latency audit-event'а, выше — DB-нагрузка."
        ),
    )
    audit_outbox_cb_sleep_chunk_seconds: float = Field(
        default=5.0,
        gt=0.0,
        alias="AUDIT_OUTBOX_CB_SLEEP_CHUNK_SECONDS",
        description=(
            "В open-state shared circuit breaker'а publisher loop спит "
            "порциями ≤ этой длины, чтобы быстро отреагировать на закрытие "
            "breaker'а и не блокировать graceful shutdown."
        ),
    )

    # ── SSH per-host backpressure ────────────────────────────────────────
    # Сколько одновременных SSH-подключений worker открывает к ОДНОМУ хосту.
    # Без лимита burst из N задач на один сервер (массовый provision +
    # inventory одного бокса) открывает N параллельных сессий, упирается в
    # sshd `MaxStartups` (дефолт 10:30:100 — после 10 неаутентифицированных
    # коннектов начинается random-drop), и worker отвечает на drop'ы
    # ретраями — амплификация. Семафор на (host, port) сериализует коннекты
    # сверх лимита; к разным хостам сессии по-прежнему параллельны.
    # Дефолт 4 — заметно ниже sshd-дефолта, оставляет запас под ручные
    # сессии оператора и не душит нормальный fan-out по разным серверам.
    ssh_max_sessions_per_host: int = Field(
        default=4,
        ge=1,
        le=64,
        alias="SSH_MAX_SESSIONS_PER_HOST",
        description=(
            "Max concurrent SSH connections the worker opens to a single "
            "(host, port). Connects beyond the limit serialize on a per-host "
            "semaphore so a burst of tasks on one server doesn't trip sshd "
            "MaxStartups and amplify into retries. Sessions to different "
            "hosts stay parallel. Default 4 — below the sshd 10:30:100 default."
        ),
    )

    # ── Management user / bootstrap (prepare) ────────────────────────────
    # Бутстрап управления (#14): задача `server.prepare` заходит на сервер
    # под одноразовыми bootstrap-кредами (password-auth), заводит системного
    # управляющего пользователя DBOS, кладёт ему публичный ключ и даёт sudo.
    # Дальше все управляющие сессии должны идти по этому ключу без исходного
    # пароля.
    ssh_management_user: str = Field(
        default="dbos",
        description=(
            "System user the worker creates on `server.prepare` to manage the "
            "host afterwards. Gets sudo and the management public key in its "
            "authorized_keys."
        ),
    )
    # После того как управляющий пользователь заведён и вход по его ключу
    # проверен живым коннектом, prepare хардит sshd через drop-in
    # /etc/ssh/sshd_config.d/*.conf: выключает парольную аутентификацию и
    # root-login. Анти-локаут: хардинг идёт ТОЛЬКО после успешной проверки
    # ключа — если она не прошла, sshd не трогаем. Флаг позволяет отключить
    # хардинг там, где парольный SSH ещё нужен (например, общий бокс с
    # ручным доступом оператора по паролю).
    ssh_harden_after_prepare: bool = Field(
        default=True,
        description=(
            "After the management user is created and key-login is verified, "
            "drop a sshd hardening snippet (PasswordAuthentication no, "
            "PermitRootLogin no, PubkeyAuthentication yes) into "
            "/etc/ssh/sshd_config.d/ and reload sshd. Disabled keeps password "
            "SSH enabled on the box."
        ),
    )

    # ── ACS snapshots (Clonezilla-обёртка) ────────────────────────────────
    # `acs.snapshot_create`/`acs.snapshot_restore` зовут ACS напрямую (не
    # через server_service-прокси) и дальше сами поллят reachability
    # сервера — ACS ребутит бокс в Clonezilla-окружение и обратно, worker
    # должен пережить это окно одной задачей.
    acs_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description=(
            "Timeout on the ACS save-disk/restore-backup HTTP call itself. "
            "ACS отвечает сразу (задача асинхронная на её стороне), поэтому "
            "долгий timeout тут не нужен — 30s с запасом на медленный TLS/DNS."
        ),
    )
    acs_reachability_poll_interval_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description=(
            "Пауза между пробами ping/ssh при ожидании ухода сервера в "
            "Clonezilla и возврата обратно после ACS snapshot/restore."
        ),
    )
    acs_down_wait_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description=(
            "Сколько ждать, что сервер уйдёт в Clonezilla (перестанет отвечать "
            "на ping/ssh) после того, как ACS приняла save-disk/restore-backup. "
            "Best-effort: если не дождались — не валим задачу, а идём сразу к "
            "ожиданию возврата (реальный ребут мог случиться между двумя "
            "тиками поллинга)."
        ),
    )
    acs_reachability_timeout_seconds: float = Field(
        default=1800.0,
        gt=0.0,
        description=(
            "Общий дедлайн ожидания, что сервер снова ответит на ping/ssh "
            "после ACS snapshot/restore. По опыту снятие/восстановление "
            "полного образа диска занимает 10-30 минут (иногда 40+ для "
            "больших дисков) — дефолт 30 минут с запасом. Не дождались — "
            "задача завершается ошибкой."
        ),
    )
    acs_bootstrap_verify_retries: int = Field(
        default=10,
        ge=1,
        description=(
            "Сколько раз пробовать реальный SSH-логин bootstrap-кредой "
            "версии ОС после того, как reachability подтвердила открытый "
            "SSH-порт (только restore). Открытый порт ещё не значит, что "
            "sshd/PAM/сеть внутри гостя полностью стабилизировались сразу "
            "после reimage — несколько попыток со sleep между ними снимают "
            "этот флаппинг."
        ),
    )
    acs_bootstrap_verify_interval_seconds: float = Field(
        default=20.0,
        gt=0.0,
        description="Пауза между попытками SSH-логина bootstrap-кредой (restore).",
    )

    # ── Interactive SSH console (WebSocket bridge) ───────────────────────
    # Долгоживущая PTY-сессия: server_service публикует `start` на
    # `console:ctl:<sid>`, worker открывает SSH invoke_shell под управляющим
    # ключом и мостит ввод/вывод через Redis pub/sub. Сессия закрывается по
    # `stop`-сигналу, WS-disconnect'у либо таймауту бездействия.
    console_idle_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "Idle timeout for an interactive SSH console PTY session. If no "
            "input arrives from the client within this window, the worker "
            "tears the session down and emits ssh_console.session_close with "
            "reason=idle_timeout."
        ),
    )
    console_max_session_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "Hard cap on a single console session lifetime regardless of "
            "activity. Bounds resource usage of a forgotten open terminal."
        ),
    )
    console_max_command_length: int = Field(
        default=8192,
        ge=1,
        description=(
            "Max bytes of a single console command line buffered before an "
            "Enter; longer input is force-flushed (audited, truncated) to "
            "bound memory of the line accumulator."
        ),
    )

    # ── Retention / bounded growth ───────────────────────────────────────
    # Terminal task'и (SUCCEEDED/FAILED) накапливаются в `tasks` навсегда —
    # таблица растёт линейно по числу dispatch'ей. Periodic
    # `tasks.cleanup_completed_old` дропает row'ы старше порога раз в сутки.
    # 30 дней — компромисс между «оператор хочет видеть историю за месяц
    # в UI» и «индекс по (status, enqueued_at) не должен распухать на 100k+
    # row'ов». Завышать стоит только если есть требования compliance —
    # audit-trail всё равно живёт в loging_service.
    tasks_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "How many days to keep SUCCEEDED/FAILED tasks in the worker DB "
            "before the daily cleanup drops them. QUEUED/RUNNING are never "
            "touched (orphan-sweep handles those). Set lower in dev/test "
            "if the table grows fast."
        ),
    )
    # Audit outbox-row'ы остаются после успешного publish'а и после DLQ-
    # отбраковки. Symmetрично tasks — без cleanup'а таблица растёт
    # линейно. 90 дней — заметно больше `tasks_retention_days`: outbox-row
    # это immediate-forensics surface на случай «loging_service не получил
    # событие, надо посмотреть payload и last_error».
    audit_outbox_retention_days: int = Field(
        default=90,
        ge=1,
        description=(
            "How many days to keep published outbox rows (both delivered "
            "and DLQ-poisoned) before the daily cleanup drops them. "
            "Unpublished (in-flight) rows are never touched by cleanup."
        ),
    )

    # ── Dispatch outbox publisher ────────────────────────────────────────
    # Publisher читает `dispatch_outbox` из server_service-БД, шлёт задачи в
    # taskiq broker и помечает row dispatched. Параметры зеркалят audit-
    # outbox publisher'а (poll-interval, batch, max_attempts, retention),
    # но семантика проще: вместо HTTP в loging_service — locally kicker.kiq.
    dispatch_outbox_poll_interval_seconds: int = Field(
        default=2,
        ge=1,
        description=(
            "Как часто publisher опрашивает dispatch_outbox. Меньше — ниже "
            "latency dispatch'а после commit'а, выше — DB-нагрузка."
        ),
    )
    dispatch_outbox_batch_size: int = Field(
        default=100,
        ge=1,
        description=(
            "Сколько строк за один проход publisher тянет FOR UPDATE SKIP "
            "LOCKED. При burst'е dispatch'ей увеличить."
        ),
    )
    dispatch_outbox_max_attempts: int = Field(
        default=10,
        ge=1,
        description=(
            "Soft-cap по `attempts` для одной outbox-строки. После cap'а "
            "publisher оставляет row с attempts=MAX и пишет WARNING — DLQ-"
            "style, без отдельной таблицы. Re-attempt — оператор сбрасывает "
            "вручную."
        ),
    )
    dispatch_outbox_retention_days: int = Field(
        default=7,
        ge=1,
        description=(
            "Сколько дней хранить успешно dispatch'нутые outbox-row'ы перед "
            "daily cleanup'ом. Unpublished/недоставленные строки не трогаются."
        ),
    )

    @model_validator(mode="after")
    def _require_worker_bot_token_outside_dev(self) -> "Settings":
        """Пустой `WORKER_BOT_TOKEN` валит старт везде, кроме local/test.

        Без PAT каждый internal-вызов в server_service возвращает 401, и
        worker молча выдаёт `IPMI_CREDENTIALS_UNAVAILABLE` /
        `ACCOUNT_PASSWORD_UNAVAILABLE` без подсказки про корневую причину.
        В dev/staging/production это материальная дыра — все power/SSH
        задачи фейлятся одинаково, оператор тратит час на диагностику.

        В `local`/`test` пусто разрешено — conftest и dev-стек поднимаются
        без secret-rotation, а HTTP-моки на стороне тестов не проверяют
        Authorization.
        """
        if self.app_env.lower() in ("local", "test"):
            return self
        if not self.worker_bot_token:
            raise ValueError(
                "WORKER_BOT_TOKEN env required "
                f"(empty token would cause every server_service call to fail "
                f"with 401 in app_env={self.app_env!r})"
            )
        return self

    @model_validator(mode="after")
    def _require_redis_auth_in_prod(self) -> "Settings":
        """В продакшене REDIS_URL обязан содержать password.

        Без AUTH'а любой pod в той же Docker/k8s сети может `RPUSH` payload
        в taskiq очередь и инициировать power-cycle/password-rotation от
        имени воркера (privilege escalation). В non-prod (`local`/`dev`/
        `test`) ограничение мягкое — чтобы CI/dev-стек поднимался без
        secret-rotation.
        """
        if self.app_env.lower() == "production" and not _REDIS_URL_PASSWORD_RE.search(self.redis_url):
            raise ValueError(
                "REDIS_URL must contain a password in production "
                "(format: redis://:<password>@host:port/db). "
                "Anonymous Redis exposes the taskiq queue to RPUSH from any "
                "co-located pod."
            )
        return self

    @model_validator(mode="after")
    def _require_logging_api_key_in_prod(self) -> "Settings":
        """В production/staging обязателен непустой `LOGGING_SERVICE_API_KEY`.

        Без ключа `audit_client.emit` тихо дропает событие (`return` без
        HTTP-запроса, только ERROR-лог) — single line в журнале легко
        теряется среди операционного шума, а audit-канал при этом
        фактически не работает. Для compliance-чувствительной инсталляции
        это материальная дыра: внешнее SIEM не увидит ни одного события
        от worker'а, а security-чувствительные ops (rotate password, power)
        останутся незалогированными.

        `staging` гоняет тот же набор операций, что и production, и пишет в
        тот же класс приёмников аудита — пустой ключ там так же молча рвёт
        канал. Поэтому strict теперь покрывает оба окружения
        (`_AUDIT_KEY_REQUIRED_ENVS`). В `local`/`test`/`dev` пусто
        разрешено — CI и dev-стек не должны падать без секрета.
        """
        if (
            self.app_env.lower() in _AUDIT_KEY_REQUIRED_ENVS
            and not self.logging_service_api_key
        ):
            raise ValueError(
                f"LOGGING_SERVICE_API_KEY must be set in {self.app_env} "
                "(empty key disables audit emission silently — "
                "loging_service won't see any event from this worker)."
            )
        return self

    @model_validator(mode="after")
    def _require_server_service_dsn_in_prod(self) -> "Settings":
        """В production `SERVER_SERVICE_DATABASE_URL` обязан быть задан и валиден.

        Publisher читает `dispatch_outbox` из server_service-БД по этому DSN.
        Пустое значение `get_session_factory()` трактует как «не сконфигурирован»
        и молча пропускает тик — в dev/test это норма, но в production это значит,
        что worker принимает задачи, а dispatch'и не публикуются: power/SSH-операции
        зависают без видимой причины. Опечатка в DSN (битый scheme, лишний пробел)
        даёт тот же тихий no-op либо падение глубоко в create_async_engine.

        Ловим оба случая на старте: пустой DSN и DSN, который SQLAlchemy не парсит.
        В non-prod пусто разрешено — worker может работать в отрыве от
        server_service-БД (только heartbeat/sweep/own-tasks).
        """
        if self.app_env.lower() != "production":
            return self
        url = (self.server_service_database_url or "").strip()
        if not url:
            raise ValueError(
                "SERVER_SERVICE_DATABASE_URL must be set in production "
                "(empty DSN makes the dispatch_outbox publisher silently skip "
                "every tick — dispatched tasks never reach the broker)."
            )
        try:
            from sqlalchemy.engine import make_url

            parsed = make_url(url)
        except Exception as exc:  # noqa: BLE001 — любую parse-ошибку трактуем как битый DSN
            raise ValueError(
                "SERVER_SERVICE_DATABASE_URL is not a valid SQLAlchemy DSN "
                f"({type(exc).__name__}); a typo would make the dispatch_outbox "
                "publisher no-op or crash on connect."
            ) from exc
        if not parsed.drivername or not parsed.database:
            raise ValueError(
                "SERVER_SERVICE_DATABASE_URL must include a driver and database name "
                f"(got drivername={parsed.drivername!r}, database={parsed.database!r})."
            )
        return self

    @model_validator(mode="after")
    def _require_https_outbound_in_prod(self) -> "Settings":
        """В production/staging исходящие URL'ы worker'а обязаны быть https.

        `LOGGING_SERVICE_URL` — канал audit-событий. На plain http внутри
        кластера любой sniff/MITM в network namespace перехватит payload
        (включая `actor_id`, `target_id`, redacted-ошибки) либо подменит
        ответ — events потеряны, SIEM ничего не увидит.

        `SERVER_SERVICE_URL` — internal API за worker-PAT. Worker таскает
        туда IPMI/SSH креды через bearer token; plain http позволяет
        зеркальному pod'у sniff'ить токен и подменять fetch_*-ответы
        (получить новый пароль iDRAC от имени worker'а).

        `AUTH_SERVICE_URL` — introspect для собственного bot-токена worker'а.
        На plain http MITM подменит `{active: true, ...}` и worker'у можно
        будет подсунуть фальшивую identity (или сорвать revoke); ответ
        полностью доверенный и в payload летит `worker_bot_token`.

        Localhost-исключение — для devcontainer / port-forward сценариев,
        где TLS терминируется на той же машине. Симметрично гарду в
        `loging_service/src/core/config.py` и
        `server_service/src/core/config.py:_require_https_auth_url_in_prod`.
        """
        if self.app_env.lower() not in _HTTPS_REQUIRED_ENVS:
            return self
        for name, url in (
            ("LOGGING_SERVICE_URL", self.logging_service_url),
            ("SERVER_SERVICE_URL", self.server_service_url),
            ("AUTH_SERVICE_URL", self.auth_service_url),
        ):
            if not url:
                continue
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            host = (parsed.hostname or "").lower()
            if scheme == "http" and not _is_intracluster_host(host):
                raise ValueError(
                    f"{name} must use https:// in {self.app_env} "
                    f"(got scheme={scheme!r}, host={host!r}); plain http "
                    "exposes worker traffic to MITM/sniff in the cluster"
                )
        return self

    @model_validator(mode="after")
    def _fallback_invalid_auto_inventory_cron(self) -> "Settings":
        """Кривой `AUTO_INVENTORY_CRON` заменяем дефолтом, а не роняем scheduler.

        taskiq на каждом тике гоняет `pycron.is_now(cron)`; строку, которую
        pycron не парсит (неверное число полей, мусор, пусто), он выкидывает
        исключением прямо в scheduler-loop — а это останавливает ВСЕ периодики
        (heartbeat, cleanup, sweep), не только auto-inventory. Опечатка
        оператора в одной env-переменной не должна стоить всего расписания:
        валидируем той же проверкой, что и taskiq, и на ошибке откатываемся на
        дефолт с WARNING'ом в лог.
        """
        expr = (self.auto_inventory_cron or "").strip()
        try:
            import pycron

            pycron.is_now(expr, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — любую parse-ошибку трактуем как битый cron
            logger.warning(
                "AUTO_INVENTORY_CRON=%r is not a valid cron expression (%s); "
                "falling back to default %r",
                self.auto_inventory_cron,
                type(exc).__name__,
                _AUTO_INVENTORY_CRON_DEFAULT,
            )
            self.auto_inventory_cron = _AUTO_INVENTORY_CRON_DEFAULT
        else:
            self.auto_inventory_cron = expr
        return self

    @model_validator(mode="after")
    def _fallback_invalid_power_sweep_cron(self) -> "Settings":
        """Кривой `POWER_SWEEP_CRON` заменяем дефолтом, а не роняем scheduler.

        Та же защита, что и у `_fallback_invalid_auto_inventory_cron`: строка,
        которую pycron не парсит, останавливает весь scheduler-loop, поэтому на
        parse-ошибке откатываемся на дефолт с WARNING'ом.
        """
        expr = (self.power_sweep_cron or "").strip()
        try:
            import pycron

            pycron.is_now(expr, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — любую parse-ошибку трактуем как битый cron
            logger.warning(
                "POWER_SWEEP_CRON=%r is not a valid cron expression (%s); "
                "falling back to default %r",
                self.power_sweep_cron,
                type(exc).__name__,
                _POWER_SWEEP_CRON_DEFAULT,
            )
            self.power_sweep_cron = _POWER_SWEEP_CRON_DEFAULT
        else:
            self.power_sweep_cron = expr
        return self

    @model_validator(mode="after")
    def _fallback_invalid_vm_status_cron(self) -> "Settings":
        """Кривой `VM_STATUS_CRON` заменяем дефолтом, а не роняем scheduler.

        Та же защита, что и у соседних cron-валидаторов: невалидную строку
        pycron выбрасывает исключением в scheduler-loop, останавливая ВСЕ
        периодики — поэтому на parse-ошибке откатываемся на дефолт с WARNING'ом.
        """
        expr = (self.vm_status_cron or "").strip()
        try:
            import pycron

            pycron.is_now(expr, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — любую parse-ошибку трактуем как битый cron
            logger.warning(
                "VM_STATUS_CRON=%r is not a valid cron expression (%s); "
                "falling back to default %r",
                self.vm_status_cron,
                type(exc).__name__,
                _VM_STATUS_CRON_DEFAULT,
            )
            self.vm_status_cron = _VM_STATUS_CRON_DEFAULT
        else:
            self.vm_status_cron = expr
        return self

    @model_validator(mode="after")
    def _fallback_invalid_vm_create_reconcile_cron(self) -> "Settings":
        """Кривой `VM_CREATE_RECONCILE_CRON` заменяем дефолтом, а не роняем scheduler.

        Та же защита, что и у соседних cron-валидаторов: невалидную строку
        pycron выбрасывает исключением в scheduler-loop, останавливая ВСЕ
        периодики — поэтому на parse-ошибке откатываемся на дефолт с WARNING'ом.
        """
        expr = (self.vm_create_reconcile_cron or "").strip()
        try:
            import pycron

            pycron.is_now(expr, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — любую parse-ошибку трактуем как битый cron
            logger.warning(
                "VM_CREATE_RECONCILE_CRON=%r is not a valid cron expression (%s); "
                "falling back to default %r",
                self.vm_create_reconcile_cron,
                type(exc).__name__,
                _VM_CREATE_RECONCILE_CRON_DEFAULT,
            )
            self.vm_create_reconcile_cron = _VM_CREATE_RECONCILE_CRON_DEFAULT
        else:
            self.vm_create_reconcile_cron = expr
        return self


@lru_cache
def get_settings() -> Settings:
    """LRU-cache'нутый getter — Settings создаются один раз за процесс."""
    return Settings()
