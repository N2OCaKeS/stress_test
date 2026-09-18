"""Настройки приложения. Все через env (pydantic-settings)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password не матчит.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Хосты, для которых разрешён plain http в prod/staging (devcontainer /
# on-host port-forward, где TLS терминируется на той же машине).
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


class Settings(BaseSettings):
    """Контейнер настроек. Поля грузятся из env (case-insensitive) и из .env-файла."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ───────────────────────────────────────────────────────────

    app_env: str = Field(
        default="local",
        description=(
            "Метка окружения: local|dev|test|staging|production. В production "
            "публичный OpenAPI surface (/openapi.json, /docs, /redoc) закрыт."
        ),
    )
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8004, ge=1, le=65535, alias="APP_PORT")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    # ── Database ──────────────────────────────────────────────────────────────

    database_url: str = Field(
        ...,
        description="PostgreSQL async DSN, например postgresql+psycopg://user:pass@host/testing_db",
    )
    db_pool_size: int = Field(default=10, ge=1, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, alias="DB_MAX_OVERFLOW")

    # ── Redis (taskiq broker для testing_worker) ─────────────────────────────

    redis_url: str = Field(
        default="redis://redis:6379/3",
        alias="REDIS_URL",
        description=(
            "Redis URL для taskiq-брокера testing_worker'а. Отдельный db-index "
            "на общем redis-контейнере: /0 — server_worker broker, /1 — "
            "auth_service rate-limit (prod), /2 — secret_service rate-limit, "
            "/3 — testing_service. В production обязателен с password."
        ),
    )
    taskiq_queue_name: str = Field(
        default="testing_taskiq",
        alias="TASKIQ_QUEUE_NAME",
        description="Имя очереди taskiq (Redis-список), которую слушает testing_worker.",
    )

    # ── Auth service (introspect для входящих токенов) ────────────────────────

    auth_service_url: str = Field(
        ...,
        description="Base URL auth_service для introspect-вызовов.",
    )
    auth_request_timeout_seconds: float = Field(default=3.0)
    introspect_service_api_key: str = Field(
        default="",
        alias="INTROSPECT_SERVICE_API_KEY",
        description=(
            "Bearer-ключ для исходящих /authorization/introspect вызовов к "
            "auth_service. Должен совпадать с одним из значений в "
            "SERVICE_API_KEYS у auth_service."
        ),
    )
    introspect_tls_verify: bool = Field(
        default=True,
        alias="INTROSPECT_TLS_VERIFY",
        description=(
            "Проверять TLS-сертификат auth_service'а. В non-loopback "
            "production/staging обязательно True."
        ),
    )
    introspect_tls_ca_bundle: str = Field(
        default="",
        alias="INTROSPECT_TLS_CA_BUNDLE",
        description="Путь до пользовательского CA bundle для introspect-вызовов (опционально).",
    )

    # ── Logging service (аудит) ────────────────────────────────────────────────

    logging_service_url: str = Field(
        default="",
        alias="LOGGING_SERVICE_URL",
        description="Base URL loging_service для отправки audit-событий. Пусто отключает удалённый аудит.",
    )
    logging_service_api_key: str = Field(
        default="",
        alias="LOGGING_SERVICE_API_KEY",
        description="Service-to-service ключ для POST /api/logging/v1/events.",
    )

    # ── Server service (internal-вызовы: prepare-for-test, acquire/release, ─────
    # test-credentials — волна 5 плана миграции; поле заводится сразу).

    server_service_url: str = Field(
        default="",
        alias="SERVER_SERVICE_URL",
        description=(
            "Base URL server_service. Сейчас используется резолверами "
            "choices_source (каталог OS-версий и ядер); дальше — "
            "prepare-for-test, acquire-for-service/release-for-service, "
            "test-credentials proxy."
        ),
    )
    server_service_api_key: str = Field(
        default="",
        alias="SERVER_SERVICE_API_KEY",
        description=(
            "Bearer для исходящих вызовов в server_service. Каталожные пути "
            "(`/os-versions`) там гейтятся introspect'ом, а не whitelist'ом "
            "сервисов, поэтому сюда кладётся токен, валидный для auth_service: "
            "бот-токен testing_service (`dbos_bot_…`) либо PAT."
        ),
    )
    server_request_timeout_seconds: float = Field(
        default=5.0,
        alias="SERVER_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут одного исходящего вызова в server_service.",
    )
    os_kernel_discovery_timeout_seconds: float = Field(default=120.0, gt=0, alias="OS_KERNEL_DISCOVERY_TIMEOUT_SECONDS")
    server_service_internal_api_key: str = Field(
        default="",
        alias="SERVER_SERVICE_INTERNAL_API_KEY",
        description=(
            "Shared s2s-секрет для КАНАЛА БРОНИ/ПОДГОТОВКИ (prepare-for-test, "
            "acquire-for-service/release-for-service/service-status, "
            "connection-info) — уходит как 'Authorization: Bearer <key>' + "
            "'X-Service-Identity: testing_service' на whitelist-эндпоинты "
            "`SERVER_INBOUND_SERVICE_API_KEYS['testing_service']` server_service. "
            "НЕ путать с `SERVER_SERVICE_API_KEY` выше: тот — бот-токен "
            "testing_service, валидный для auth_service introspect (каталоги "
            "os-versions/kernels и pass-through `GET /servers/{id}`); этот — "
            "чистый shared-secret без пользовательской identity, годный только "
            "для explicit-whitelist internal-путей server_service. Два разных "
            "механизма на два разных набора эндпоинтов, оба нужны одновременно."
        ),
    )

    pool_overview_ping_stale_seconds: int = Field(
        default=900,
        ge=1,
        alias="POOL_OVERVIEW_PING_STALE_SECONDS",
        description=(
            "Порог свежести `ping_checked_at` для обзора пула (§F плана "
            "2026-09-11). Server_worker пингует стенды раз в несколько минут "
            "(дефолт частого power-sweep — 5 минут); если последний замер "
            "старше порога либо его не было вовсе, стенд показывается "
            "«Нет данных», а не выдуманным «недоступен»/«готов»."
        ),
    )

    # ── Secret service (reveal Jira/Zephyr/Confluence-кред department_integration_settings, §6) ─

    secret_service_url: str = Field(
        default="",
        alias="SECRET_SERVICE_URL",
        description="Base URL secret_service для POST /credentials/{id}/reveal.",
    )
    secret_service_api_key: str = Field(
        default="",
        alias="SECRET_SERVICE_API_KEY",
        description=(
            "Bearer для исходящих вызовов в secret_service — бот-токен "
            "testing_service (`dbos_bot_…`), валидный для auth_service introspect. "
            "Тот же принцип, что `SERVER_SERVICE_API_KEY`: reveal гейтится обычной "
            "identity-based авторизацией secret_service, не shared s2s-секретом."
        ),
    )
    secret_request_timeout_seconds: float = Field(
        default=5.0,
        alias="SECRET_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут одного исходящего вызова в secret_service.",
    )

    # ── Changelog service (фильтрация СТП по изменившимся компонентам, §7) ───

    changelog_service_url: str = Field(
        default="",
        alias="CHANGELOG_SERVICE_URL",
        description=(
            "Base URL сервиса changelog (легаси — http://10.177.103.10:8989). "
            "Пусто — фильтрация по компонентам отключена, `/stp/generate` берёт "
            "полный набор тестов независимо от RC/final."
        ),
    )
    changelog_request_timeout_seconds: float = Field(
        default=5.0,
        alias="CHANGELOG_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут запроса к changelog-сервису.",
    )
    changelog_cache_ttl_seconds: float = Field(
        default=7776000.0,  # 90 дней — build_version не переиздаётся задним числом.
        ge=1.0,
        alias="CHANGELOG_CACHE_TTL_SECONDS",
        description=(
            "TTL строки `changelog_cache` по build_version (§2.7/§7 плана "
            "миграции). Практически бессрочный: changelog собранного RC не "
            "меняется, дефолт — 90 дней. Устаревшая или отсутствующая запись "
            "триггерит повторный запрос к changelog-сервису."
        ),
    )

    # ── Zephyr Scale / Jira ATM (§6.1-6.2 плана миграции) ────────────────────

    zephyr_request_timeout_seconds: float = Field(
        default=10.0,
        alias="ZEPHYR_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут одного исходящего вызова в Jira/Zephyr Scale ATM REST API.",
    )

    # ── Confluence (§2.7, §9.2 плана миграции — end-of-run комментарий) ──────

    confluence_request_timeout_seconds: float = Field(
        default=10.0,
        alias="CONFLUENCE_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут одного исходящего вызова в Confluence REST API.",
    )

    # ── Bitbucket / Tempo (§9.1 плана миграции — HR-отчёт по активности) ─────

    bitbucket_request_timeout_seconds: float = Field(
        default=10.0,
        alias="BITBUCKET_REQUEST_TIMEOUT_SECONDS",
        description="Таймаут одного исходящего вызова в Bitbucket Server REST API.",
    )
    tempo_request_timeout_seconds: float = Field(
        default=15.0,
        alias="TEMPO_REQUEST_TIMEOUT_SECONDS",
        description=(
            "Таймаут вызова Tempo worklogs/search — за месяц может вернуться "
            "много записей, дефолт выше, чем у остальных интеграций."
        ),
    )

    # ── Statistics service (§2.7, §9.3 плана миграции — фоновый пересчёт статистики) ─

    statistics_request_timeout_seconds: float = Field(
        default=300.0,
        alias="STATISTICS_REQUEST_TIMEOUT_SECONDS",
        description=(
            "Таймаут POST /all-statistics внешнего сервиса статистики (ветка "
            "`statistics` этого же монорепо, `statistics/main_api.py`). Все "
            "его эндпоинты синхронные (`def`, не `async def`) и пересчитывают "
            "много семейств тестов последовательно в одном запросе — может "
            "идти минутами, поэтому таймаут заметно выше, чем у остальных "
            "интеграций сервиса. Вызов всегда идёт из фоновой задачи "
            "(`services/statistics_recalc.py`), не из request-response цикла."
        ),
    )

    # ── Redis creds stash (креды тестового пользователя между callback'ом ────
    # prepare-for-test и claim'ом testing_worker'а) ───────────────────────────

    creds_stash_ttl_seconds: int = Field(
        default=600,
        ge=1,
        alias="CREDS_STASH_TTL_SECONDS",
        description=(
            "TTL одноразовой Redis-записи с test_username/test_password/"
            "test_ssh_private_key между успешным callback'ом prepare-for-test "
            "и тем моментом, когда testing_worker заберёт их через "
            "`POST /internal/queue/claim`. Очередь по стенду сериализована, "
            "поэтому воркер должен успеть забрать готовый элемент быстро — "
            "10 минут щедрый запас. Не шифруется отдельным конвертом (в "
            "отличие от `REDIS_STASH_ENCRYPTION_KEY` у server_service): это "
            "уже эфемерные креды с коротким временем жизни и одноразовым "
            "ключом, чего для этой волны достаточно."
        ),
    )

    # ── Inbound service-to-service auth ───────────────────────────────────────

    service_api_key: str = Field(
        default="",
        alias="SERVICE_API_KEY",
        description=(
            "Legacy fallback shared key для входящих internal-вызовов. "
            "Deprecated в пользу SERVICE_API_KEYS map; оставлен на время "
            "миграции существующих caller'ов."
        ),
    )
    service_api_keys: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict,
        alias="SERVICE_API_KEYS",
        description=(
            "Per-service map: имя caller'а → его inbound API-key. Формат "
            "значения env: JSON-объект или comma-separated 'svc:key,svc:key'. "
            "`NoDecode` — иначе pydantic-settings пытается JSON.loads() значение "
            "до вызова валидатора ниже и падает на kv-list формате."
        ),
    )

    # ── Rate-limit ────────────────────────────────────────────────────────────

    slowapi_rate_limit: str = Field(
        default="120/second",
        alias="SLOWAPI_RATE_LIMIT",
        description="Глобальный per-IP rate-limit (slowapi syntax). Health/ready исключены.",
    )
    rate_limit_storage_uri: str = Field(
        default="memory://",
        alias="RATE_LIMIT_STORAGE_URI",
        description=(
            "Хранилище счётчиков slowapi. 'memory://' — per-process. Для "
            "multi-pod используется redis://... — счётчики общие на fleet."
        ),
    )
    redis_password: str = Field(
        default="",
        alias="REDIS_PASSWORD",
        description="Пароль Redis (если RATE_LIMIT_STORAGE_URI использует redis://).",
    )

    # ── Security headers ──────────────────────────────────────────────────────

    security_hsts_enabled: bool = Field(
        default=False,
        alias="SECURITY_HSTS_ENABLED",
        description="Включает Strict-Transport-Security. Только за https-фронтом.",
    )
    swagger_ui_assets_base: str = Field(
        default="",
        alias="SWAGGER_UI_ASSETS_BASE",
        description=(
            "Базовый URL self-host бандла Swagger UI. Пусто → /docs тянет "
            "ассеты с CDN jsdelivr. Задан → /docs раздаётся своим маршрутом."
        ),
    )

    worker_log_level: str = Field(default="INFO", alias="WORKER_LOG_LEVEL")

    # ── Логи прогонов: ротация (§8.5 плана миграции) ─────────────────────────

    log_retention_days: int = Field(
        default=30,
        ge=1,
        alias="LOG_RETENTION_DAYS",
        description=(
            "Сколько дней хранить незащищённый (`protected=false`) лог "
            "прогона, прежде чем фоновая суточная job его удалит. Защищённые "
            "логи (2 самых свежих RC на своей ветке) этой политике не "
            "подчиняются, пока не появится следующий RC."
        ),
    )
    log_rotation_interval_seconds: float = Field(
        default=86400.0,
        ge=1.0,
        alias="LOG_ROTATION_INTERVAL_SECONDS",
        description=(
            "Период фонового `asyncio`-цикла ротации логов в lifespan "
            "процесса (см. `main.py`). testing_service — чистый FastAPI без "
            "своего брокера/scheduler'а, поэтому это обычный "
            "`asyncio.create_task`, не taskiq-задача; суточной точности "
            "достаточно (план §8.5 явно не требует ежеминутной)."
        ),
    )

    # ── HR-отчёт по активности: авто-генерация 1 числа месяца ────────────────

    activity_report_auto_generate_interval_seconds: float = Field(
        default=3600.0,
        ge=1.0,
        alias="ACTIVITY_REPORT_AUTO_GENERATE_INTERVAL_SECONDS",
        description=(
            "Период фонового `asyncio`-цикла проверки авто-генерации HR-отчёта "
            "(см. `main.py::_activity_report_auto_generate_loop`). Тот же "
            "приём, что и у ротации логов — свой цикл в lifespan вместо "
            "taskiq-scheduler'а. Часовой период достаточен: тик, попавший на "
            "1 число месяца, запускает генерацию; повторные тики в тот же "
            "день — no-op благодаря проверке уже существующего отчёта."
        ),
    )

    # ── Audit outbox (durable доставка audit-событий) ─────────────────────────

    audit_outbox_poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.1,
        alias="AUDIT_OUTBOX_POLL_INTERVAL_SECONDS",
        description=(
            "Период фонового цикла доставки audit-событий из таблицы "
            "`audit_outbox` в loging_service (см. "
            "`services/audit_outbox_publisher.run_drain_loop`). Тот же приём, "
            "что у ротации логов — свой `asyncio`-цикл в lifespan."
        ),
    )
    audit_outbox_batch_size: int = Field(
        default=20,
        ge=1,
        le=500,
        alias="AUDIT_OUTBOX_BATCH_SIZE",
        description=(
            "Сколько строк outbox'а drain-цикл пытается доставить за один "
            "проход. Строки обрабатываются по одной (SELECT ... LIMIT 1 FOR "
            "UPDATE SKIP LOCKED + commit), это верхняя граница итераций."
        ),
    )
    audit_outbox_max_publish_attempts: int = Field(
        default=50,
        ge=1,
        alias="AUDIT_OUTBOX_MAX_PUBLISH_ATTEMPTS",
        description=(
            "Сколько неудачных попыток доставки терпит строка, прежде чем "
            "уехать в DLQ (`published_at` проставлен, `last_error` начинается "
            "с `[DLQ:attempts_cap]`). С backoff'ом 2^attempts (cap 300s) это "
            "порядка нескольких часов недоступности loging_service."
        ),
    )
    audit_outbox_retention_hours: int = Field(
        default=24,
        ge=1,
        alias="AUDIT_OUTBOX_RETENTION_HOURS",
        description=(
            "Сколько часов доставленные (и DLQ-помеченные) строки лежат в "
            "`audit_outbox`, прежде чем их снесёт cleanup drain-цикла. "
            "Недоставленные не трогаются никогда."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("service_api_keys", mode="before")
    @classmethod
    def _parse_service_api_keys(cls, v):
        """Поддерживает JSON-объект и 'svc:key,svc:key' в дополнение к нативному dict."""
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return {}
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"SERVICE_API_KEYS: invalid JSON ({exc})") from exc
                if not isinstance(parsed, dict):
                    raise ValueError("SERVICE_API_KEYS: JSON must be an object")
                return {str(k): str(v) for k, v in parsed.items()}
            out: dict[str, str] = {}
            for pair in text.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                if ":" not in pair:
                    raise ValueError(
                        f"SERVICE_API_KEYS: expected 'svc:key' segments, got {pair!r}"
                    )
                name, key = pair.split(":", 1)
                name, key = name.strip(), key.strip()
                if not name or not key:
                    raise ValueError(f"SERVICE_API_KEYS: empty name or key in {pair!r}")
                out[name] = key
            return out
        return v

    @model_validator(mode="after")
    def _require_https_auth_url_in_prod(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        parsed = urlparse(self.auth_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and not _is_intracluster_host(host):
            raise ValueError(
                "AUTH_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r})"
            )
        return self

    @model_validator(mode="after")
    def _require_https_logging_url_in_prod(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        if not self.logging_service_url:
            return self
        parsed = urlparse(self.logging_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and not _is_intracluster_host(host):
            raise ValueError(
                "LOGGING_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r})"
            )
        return self

    @model_validator(mode="after")
    def _require_https_server_service_url_in_prod(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        if not self.server_service_url:
            return self
        parsed = urlparse(self.server_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and not _is_intracluster_host(host):
            raise ValueError(
                "SERVER_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r})"
            )
        return self

    @model_validator(mode="after")
    def _require_introspect_key_in_prod(self) -> "Settings":
        if (
            self.app_env.lower() in {"production", "staging"}
            and not self.introspect_service_api_key
        ):
            raise ValueError(
                f"INTROSPECT_SERVICE_API_KEY must be set in {self.app_env}; "
                "empty key makes every introspect call to auth_service fail with 401"
            )
        return self

    @model_validator(mode="after")
    def _forbid_introspect_tls_skip_on_remote(self) -> "Settings":
        if self.introspect_tls_verify:
            return self
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        parsed = urlparse(self.auth_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme != "https":
            return self
        if _is_intracluster_host(host):
            return self
        raise ValueError(
            "INTROSPECT_TLS_VERIFY=false is forbidden on non-loopback "
            f"AUTH_SERVICE_URL in {self.app_env} (host={host!r}); MITM-risk "
            "on cluster network"
        )

    @model_validator(mode="after")
    def _require_redis_auth_in_prod(self) -> "Settings":
        if self.app_env.lower() != "production":
            return self
        if not _REDIS_URL_PASSWORD_RE.search(self.redis_url):
            raise ValueError(
                "REDIS_URL must contain a password in production "
                "(format: redis://:<password>@host:port/db). Anonymous Redis "
                "exposes the taskiq queue to RPUSH from any co-located pod."
            )
        return self

    @model_validator(mode="after")
    def _require_redis_rate_limit_auth_in_prod(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        if not self.rate_limit_storage_uri.startswith("redis"):
            return self
        if _REDIS_URL_PASSWORD_RE.search(self.rate_limit_storage_uri):
            return self
        raise ValueError(
            "RATE_LIMIT_STORAGE_URI must contain a password in "
            f"{self.app_env} when using redis:// "
            "(format: redis://:<password>@host:port/db)"
        )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный фабричный метод. Один Settings на процесс."""
    return Settings()
