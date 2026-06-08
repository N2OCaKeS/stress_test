"""Настройки приложения. Все через env (pydantic-settings)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password не матчит.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Хосты, для которых разрешён plain http в prod/staging (devcontainer /
# on-host port-forward, где TLS терминируется на той же машине).
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


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
    app_port: int = Field(default=8003, ge=1, le=65535, alias="APP_PORT")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    # ── Database ──────────────────────────────────────────────────────────────

    database_url: str = Field(
        ...,
        description="PostgreSQL async DSN, например postgresql+psycopg://user:pass@host/secret_db",
    )
    db_pool_size: int = Field(default=10, ge=1, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, alias="DB_MAX_OVERFLOW")

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

    # ── Logging service (опциональный удалённый аудит) ────────────────────────

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

    # ── Encryption ────────────────────────────────────────────────────────────

    secret_encryption_key: str = Field(
        ...,
        min_length=32,
        alias="SECRET_ENCRYPTION_KEY",
        description=(
            "Мастер-ключ симметричного шифрования секретов (integration tokens, "
            "service credentials). HKDF-SHA256 деривация AES-256-GCM ключа. "
            "Сгенерить: openssl rand -base64 48."
        ),
    )
    secret_encryption_key_version: int = Field(
        default=2,
        ge=2,
        alias="SECRET_ENCRYPTION_KEY_VERSION",
        description=(
            "Активная версия ключа для новых ciphertext'ов (формат v<N>$...). "
            "Версия 1 — legacy SHA-256; новые записи только под v2+."
        ),
    )
    hkdf_salt_hex: str = Field(
        default="",
        alias="HKDF_SALT_HEX",
        description=(
            "HKDF-salt в hex (минимум 32 hex = 16 байт). В production/staging "
            "обязателен — без него два деплоя получают одинаковый KDF-output."
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
    service_api_keys: dict[str, str] = Field(
        default_factory=dict,
        alias="SERVICE_API_KEYS",
        description=(
            "Per-service map: имя caller'а → его inbound API-key. Формат "
            "значения env: JSON-объект или comma-separated 'svc:key,svc:key'."
        ),
    )

    # ── Rate-limit ────────────────────────────────────────────────────────────

    slowapi_rate_limit: str = Field(
        default="500/minute",
        alias="SLOWAPI_RATE_LIMIT",
        description=(
            "Глобальный per-IP rate-limit (slowapi syntax). Health/ready "
            "исключены."
        ),
    )
    rate_limit_storage_uri: str = Field(
        default="memory://",
        alias="RATE_LIMIT_STORAGE_URI",
        description=(
            "Хранилище счётчиков slowapi. 'memory://' — per-process (ок для "
            "одного pod'а). Для multi-pod используется redis://... — счётчики "
            "общие, лимит соблюдается на всём fleet."
        ),
    )
    redis_password: str = Field(
        default="",
        alias="REDIS_PASSWORD",
        description=(
            "Пароль Redis (если RATE_LIMIT_STORAGE_URI использует redis://). "
            "Подставляется в derived URIs в коде; в URI напрямую можно не "
            "светить, чтобы не утёк в логи."
        ),
    )

    # Per-endpoint rate-limits (slowapi syntax). Применяются `@limiter.limit(...)`
    # декораторами на чувствительных handler'ах поверх глобального лимита.
    # Глобальный slowapi_rate_limit остаётся spam-щитом, эти — тонкая настройка
    # на reveal / transfer / recover / delete / create / dept-grant / acl.
    rate_limit_reveal: str = Field(
        default="5/minute",
        alias="RATE_LIMIT_REVEAL",
        description=(
            "Per-IP rate-limit для POST /credentials/{id}/reveal. Основной "
            "throttle reveal'а — per-(actor, cred) внутри `reveal_throttle`; "
            "этот IP-level — защита от попыток обойти actor-throttle сменой "
            "учётки с одного хоста."
        ),
    )
    rate_limit_transfer: str = Field(
        default="10/minute",
        alias="RATE_LIMIT_TRANSFER",
        description="Per-IP rate-limit для POST /credentials/{id}/transfer (admin override).",
    )
    rate_limit_recover: str = Field(
        default="10/minute",
        alias="RATE_LIMIT_RECOVER",
        description="Per-IP rate-limit для POST /credentials/{id}/recover.",
    )
    rate_limit_delete: str = Field(
        default="30/minute",
        alias="RATE_LIMIT_DELETE",
        description="Per-IP rate-limit для DELETE /credentials/{id}.",
    )
    rate_limit_create: str = Field(
        default="60/minute",
        alias="RATE_LIMIT_CREATE",
        description="Per-IP rate-limit для POST /credentials.",
    )
    rate_limit_dept_grant: str = Field(
        default="20/minute",
        alias="RATE_LIMIT_DEPT_GRANT",
        description="Per-IP rate-limit для POST /credentials/{id}/dept-grants.",
    )
    rate_limit_acl: str = Field(
        default="30/minute",
        alias="RATE_LIMIT_ACL",
        description="Per-IP rate-limit для POST /credentials/{id}/acl.",
    )

    # ── Lockout (per-user denied-access throttle) ─────────────────────────────

    lockout_threshold: int = Field(
        default=10,
        ge=1,
        alias="LOCKOUT_THRESHOLD",
        description=(
            "Сколько denied access'ов в окне `LOCKOUT_WINDOW_SECONDS` "
            "переводят user'а в lock. Lock — `LOCKOUT_DURATION_SECONDS` "
            "секунд. Применяется в `services/lockout_service.py`."
        ),
    )
    lockout_window_seconds: int = Field(
        default=300,
        ge=1,
        alias="LOCKOUT_WINDOW_SECONDS",
        description="Скользящее окно учёта denied-failures (сек).",
    )
    lockout_duration_seconds: int = Field(
        default=900,
        ge=1,
        alias="LOCKOUT_DURATION_SECONDS",
        description="Длительность lockout'а после превышения порога (сек).",
    )

    # ── Security headers ──────────────────────────────────────────────────────

    security_hsts_enabled: bool = Field(
        default=False,
        alias="SECURITY_HSTS_ENABLED",
        description="Включает Strict-Transport-Security. Только за https-фронтом.",
    )

    # ── Lifecycle / sweep ─────────────────────────────────────────────────────

    blocked_retention_days: int = Field(
        default=30,
        ge=1,
        alias="BLOCKED_RETENTION_DAYS",
        description=(
            "Окно (в днях) от blocked_at до hard delete. После истечения "
            "sweep уносит блокированную креду без права recover'а."
        ),
    )
    sweep_interval_seconds: int = Field(
        default=3600,
        ge=10,
        alias="SWEEP_INTERVAL_SECONDS",
        description="Интервал между прогонами sweep-loop'а (сек).",
    )
    sweep_enabled: bool = Field(
        default=True,
        alias="SWEEP_ENABLED",
        description=(
            "Запускать ли sweep-loop в lifespan'е. В tests/local-dev'е удобно "
            "выключать, чтобы не плодить фоновые task'и."
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
                    raise ValueError(
                        f"SERVICE_API_KEYS: empty name or key in {pair!r}"
                    )
                out[name] = key
            return out
        return v

    @field_validator("hkdf_salt_hex", mode="after")
    @classmethod
    def _validate_hkdf_salt_format(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        if len(v) < 32:
            raise ValueError(
                f"HKDF_SALT_HEX must be at least 32 hex chars (16 bytes), got {len(v)}"
            )
        try:
            bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError(f"HKDF_SALT_HEX must be valid hex: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _require_hkdf_salt_in_prod(self) -> "Settings":
        if self.app_env.lower() in {"production", "staging"} and not self.hkdf_salt_hex:
            raise ValueError(
                f"HKDF_SALT_HEX must be set in {self.app_env} "
                "(min 32 hex chars). Generate: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        return self

    @model_validator(mode="after")
    def _require_min_key_version_in_prod(self) -> "Settings":
        if (
            self.app_env.lower() in {"production", "staging"}
            and self.secret_encryption_key_version < 2
        ):
            raise ValueError(
                "SECRET_ENCRYPTION_KEY_VERSION must be >= 2 in "
                f"{self.app_env} (v1 uses legacy SHA-256 without HKDF)"
            )
        return self

    @model_validator(mode="after")
    def _require_https_auth_url_in_prod(self) -> "Settings":
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        parsed = urlparse(self.auth_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and host not in _LOCAL_HOSTS:
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
        if scheme == "http" and host not in _LOCAL_HOSTS:
            raise ValueError(
                "LOGGING_SERVICE_URL must use https:// in "
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
        host = (parsed.hostname or "").lower()
        if host in _LOCAL_HOSTS:
            return self
        raise ValueError(
            "INTROSPECT_TLS_VERIFY=false is forbidden on non-loopback "
            f"AUTH_SERVICE_URL in {self.app_env} (host={host!r}); MITM-risk "
            "on cluster network"
        )

    @model_validator(mode="after")
    def _require_redis_auth_in_prod(self) -> "Settings":
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
