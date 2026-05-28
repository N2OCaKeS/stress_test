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
            "password (защита taskiq queue от анонимного RPUSH из соседних подов)."
        ),
    )
    database_url: str = Field(
        ...,
        description="PostgreSQL async DSN for the worker's own DB (dev_server_worker)",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description=(
            "Redis URL for the taskiq broker. В продакшене обязательно с password, "
            "формат `redis://:<password>@host:port/db`. Иначе любой контейнер "
            "в той же сети может RPUSH-нуть payload в очередь воркера."
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
    http_request_timeout_seconds: float = Field(default=5.0, description="HTTP timeout")
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
            "longer. Worker retry/back-off handles transient failures."
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
    secrets_reencrypt_interval_seconds: float = Field(
        default=300.0,
        ge=1.0,
        description=(
            "Cooldown between successive `secrets.reencrypt_lazy` ticks. "
            "Lower values speed up the migration but pressure the DB / "
            "server_service; default 5 min — компромисс под realistic "
            "RPS воркера."
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
