"""Настройки приложения."""

import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password
# (`redis://redis:6379/0`) и без user:pass-сегмента вообще — не матчит.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Hostnames, где plain `http://` допустим даже в production/staging —
# devcontainer / sidecar / on-host debug-сценарии, где TLS терминируется
# на той же машине и MITM-модель другая. Симметрично
# `loging_service/src/core/config.py:_LOCAL_HOSTS`.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

# Окружения, в которых требуется https:// для исходящих HTTP-вызовов.
_HTTPS_REQUIRED_ENVS: frozenset[str] = frozenset({"production", "staging"})


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
    # /encrypt /UPDATE row под master-key); `finalize_reencrypt_outbox_done` на
    # большом batch + slow PostgreSQL легко выйдет за 5s. 15s — компромисс
    # между «не зависать» и «не ловить ложный SERVER_SERVICE_UNREACHABLE».
    server_service_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description=(
            "HTTP timeout для internal-callback'ов в server_service "
            "(fetch_*/submit_*/reencrypt_outbox/*). Длиннее audit-timeout'а: "
            "криптооперации сервера могут тянуться 50-200ms на row, batch "
            "callback'и упираются в slow PG."
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
    # считает её orphan-кандидатом. По умолчанию 30 мин — потолок для
    # медленных задач (inventory.sync через SSH с большим dpkg-листингом).
    # Уменьшить можно для smoke-тестов: тесты sweep'а используют
    # monkeypatch на инстанс настроек.
    worker_orphan_threshold_seconds: float = Field(
        default=1800.0,
        description=(
            "Min seconds a task must stay in 'running' status before the "
            "orphan sweep considers it. Sweep also requires the task's "
            "worker_id NOT to be among recently-heartbeating workers."
        ),
    )
    # Сколько секунд heartbeat'а отсутствие worker_id считается «упал».
    # Должно быть заметно больше heartbeat-интервала (60s) — иначе
    # transient GC pause / DB hiccup сделает живой worker «мёртвым».
    worker_heartbeat_stale_seconds: float = Field(
        default=300.0,
        description=(
            "After this many seconds since last_heartbeat_at, a worker_id "
            "is considered inactive; tasks owned by it past the orphan "
            "threshold get marked failed by the sweep."
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
    # Публичный ключ кладём в authorized_keys управляющего пользователя.
    # Дефолт пустой — без него prepare откажется работать (нечего класть);
    # сам ключ задаётся через env / k8s secret, в коде не хардкодится.
    ssh_management_public_key: str = Field(
        default="",
        description=(
            "OpenSSH public key (e.g. 'ssh-ed25519 AAAA... dbos') appended to "
            "the management user's authorized_keys during `server.prepare`. "
            "Empty disables prepare (nothing to install)."
        ),
    )
    # Путь к приватному ключу для последующих management-сессий. На него в
    # будущем переключатся provision/rotate/inventory вместо self-сессии под
    # самим аккаунтом (см. follow-up). В коде не хранится — путь к файлу из
    # mounted k8s secret.
    ssh_management_private_key_path: str = Field(
        default="",
        description=(
            "Path to the management private key file (mounted secret) used "
            "for key-based management sessions after `server.prepare`. Empty "
            "in dev/test; provision/rotate still use self password-auth until "
            "they migrate to the management session."
        ),
    )

    # ── Background master-key rotation ───────────────────────────────────
    # Periodic task `secrets.reencrypt_lazy` зовёт server_service
    # `/internal/secrets/migration_status` + `/reencrypt_batch`. Активна
    # только когда `RUNNING_TASKS` пустой — чтобы не конкурировать с
    # power/SSH/inventory задачами за DB-write'ы и CPU. Дефолт интервала
    # 300s — re-encrypt latency-некритичен, но и не хочется ждать сутки
    # после bump'а активной версии ключа.
    # Дефолт намеренно `False`: без явного `SECRETS_REENCRYPT_ENABLED=true`
    # worker не дёргает `/internal/secrets/reencrypt_batch`. При `True` он
    # начал бы периодическую re-encryption с первого старта, даже когда
    # миграция ключа не запланирована — а при копировании prod-манифеста в
    # dev/staging (где `/internal/secrets/*` недостижим или ключ другой) это
    # включалось бы неожиданно. Включать осознанно на время миграции ключа.
    # Симметрично другим prod-guard-настройкам (secure-by-default).
    secrets_reencrypt_enabled: bool = Field(
        default=False,
        description=(
            "Toggle the background secret re-encryption periodic task. "
            "Default False — enable explicitly (SECRETS_REENCRYPT_ENABLED=true) "
            "only while a master-key migration is in progress, so it never "
            "fires unexpectedly when a prod manifest is copied to dev/staging."
        ),
    )
    secrets_reencrypt_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description=(
            "Размер одного `reencrypt_batch` запроса. Worker не делает "
            "несколько батчей за тик — это сохраняется на следующий "
            "интервал, чтобы high-priority задачи могли прорваться "
            "между батчами."
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
        """В production обязателен непустой `LOGGING_SERVICE_API_KEY`.

        Без ключа `audit_client.emit` тихо дропает событие (`return` без
        HTTP-запроса, только ERROR-лог) — single line в журнале легко
        теряется среди операционного шума, а audit-канал при этом
        фактически не работает. Для compliance-чувствительной инсталляции
        это материальная дыра: внешнее SIEM не увидит ни одного события
        от worker'а, а security-чувствительные ops (rotate password, power)
        останутся незалогированными.

        В non-prod (`local`/`dev`/`test`/`staging`) пусто разрешено — CI
        и dev-стек не должны падать без секрета. `staging` намеренно НЕ
        включён в strict-список: там может стоять прод-ключ, а может и
        нет (зависит от конкретного стенда), и блокировать запуск было
        бы лишним зерганием операторам.
        """
        if self.app_env.lower() == "production" and not self.logging_service_api_key:
            raise ValueError(
                "LOGGING_SERVICE_API_KEY must be set in production "
                "(empty key disables audit emission silently — "
                "loging_service won't see any event from this worker)."
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
            if scheme == "http" and host not in _LOCAL_HOSTS:
                raise ValueError(
                    f"{name} must use https:// in {self.app_env} "
                    f"(got scheme={scheme!r}, host={host!r}); plain http "
                    "exposes worker traffic to MITM/sniff in the cluster"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """LRU-cache'нутый getter — Settings создаются один раз за процесс."""
    return Settings()
