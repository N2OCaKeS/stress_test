"""Конфиг auth_service. Settings через pydantic-settings + production-guard."""

import logging
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# Hostnames, где plain http допустим даже в проде (devcontainer / sidecar /
# on-host port-forward — TLS терминируется на той же машине, MITM-модель
# другая). Зеркалит `loging_service/core/config.py:_LOCAL_HOSTS`.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    """Runtime-настройки auth_service. Читаются из ENV + опционально из `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="auth_service", alias="APP_NAME")
    app_env: Literal["local", "development", "test", "production"] = Field(
        default="local",
        alias="APP_ENV",
    )
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://auth_user:auth_password@localhost:5432/auth_db",
        alias="DATABASE_URL",
    )
    # SQLAlchemy connection pool sizing. Дефолты под dev/test-стенд:
    # 10 постоянных + 20 burst — суммарный лимит 30 соединений к БД,
    # хватает одной replica auth_service'а под нагрузкой. В прод поднимать
    # вслед за БД-конфигом (`pgbouncer pool_size`). Симметрично с
    # `loging_service`, `server_service` и `server_worker`.
    db_pool_size: int = Field(default=10, ge=1, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, alias="DB_MAX_OVERFLOW")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    access_token_ttl_minutes: int = Field(default=10, alias="ACCESS_TOKEN_TTL_MINUTES")
    refresh_token_ttl_days: int = Field(default=14, alias="REFRESH_TOKEN_TTL_DAYS")

    # Login brute-force protection: после N подряд неудач — lockout на M минут.
    # Атомарный счётчик в `users.failed_login_attempts`, lockout пишется в
    # `users.locked_until` (UTC). Сбрасывается на успешный verify или по истечении
    # `locked_until`. Дефолты ОWASP-baseline: 5 попыток / 15 минут.
    max_failed_login_attempts: int = Field(
        default=5,
        alias="MAX_FAILED_LOGIN_ATTEMPTS",
        description="Сколько подряд неудачных login'ов триггерят lockout.",
    )
    lockout_minutes: int = Field(
        default=15,
        alias="LOCKOUT_MINUTES",
        description="Длительность lockout в минутах после достижения лимита неудач.",
    )

    # Симметричные лимиты для OAuth `client_credentials` brute-force
    # (`OAuthClient.failed_secret_attempts`) и для bot-токена в
    # `/docker/token` (`BotAccount.failed_token_attempts`). Те же
    # 5 / 15 дефолты OWASP, но переопределяемы отдельно — иначе
    # завязка на user-параметры мешала бы крутить агрессивнее одно
    # без другого.
    oauth_client_max_failed_secret_attempts: int = Field(
        default=5,
        alias="OAUTH_CLIENT_MAX_FAILED_SECRET_ATTEMPTS",
        description="Сколько подряд неверных client_secret триггерят lockout клиента.",
    )
    oauth_client_lockout_minutes: int = Field(
        default=15,
        alias="OAUTH_CLIENT_LOCKOUT_MINUTES",
        description="Длительность OAuth-client lockout в минутах.",
    )
    bot_max_failed_token_attempts: int = Field(
        default=5,
        alias="BOT_MAX_FAILED_TOKEN_ATTEMPTS",
        description="Сколько подряд неверных bot-токенов триггерят lockout бота.",
    )
    bot_lockout_minutes: int = Field(
        default=15,
        alias="BOT_LOCKOUT_MINUTES",
        description="Длительность bot-lockout в минутах.",
    )

    # Окно для детектора `bot.suspicious_multi_ip`: если за это время бот
    # засветился с >=2 разных IP — CRITICAL audit.
    bot_suspicious_ip_window_seconds: int = Field(
        default=3600,
        alias="BOT_SUSPICIOUS_IP_WINDOW_SECONDS",
        description="Длина окна (сек) для детектора подозрительной мульти-IP активности бота.",
    )
    # Сколько последних (ip, ts) пар держим в `bot_accounts.last_known_ips` (FIFO).
    bot_last_known_ips_window: int = Field(
        default=5,
        alias="BOT_LAST_KNOWN_IPS_WINDOW",
        description="Размер FIFO-окна last_known_ips бота.",
    )

    # TTL для identity-cache (sha256(token) → IdentityContext) в
    # `dependencies/auth.py`. Default 5s — окно burst'а одной UI-сессии, но
    # недостаточно для долгого stale-доступа забаненного.
    # 0 / отрицательное — disable (тесты, мутирующие User прямым SQL'ом).
    identity_cache_ttl_seconds: float = Field(
        default=5.0,
        alias="IDENTITY_CACHE_TTL_SECONDS",
        description=(
            "TTL identity-кэша в секундах. 0 или меньше — кэш выключен."
        ),
    )

    # Потолок числа entries в `dependencies/auth._identity_cache`. Под
    # burst'ом коротко-живущих токенов (PAT/bot per-request) без cap'а
    # кэш растёт без границ → OOM pod'а. 50k уверенно покрывает live-JWT
    # крупного pod'а при разумном размере записи.
    identity_cache_maxsize: int = Field(
        default=50_000,
        alias="IDENTITY_CACHE_MAXSIZE",
        description="Максимум entries в identity-кэше до eviction.",
    )

    # JWT audience / issuer / clock-skew tolerance.
    #
    # `aud` и `iss` сохраняются в каждом access-токене (`create_access_token`)
    # и **обязательно** проверяются на `decode_access_token` — блокирует
    # cross-audience replay (токен с другим issuer'ом или для другой audience
    # отвергается). `leeway` (секунды) даёт допуск на clock-skew между
    # replicas — без него NTP-дрейф в 1-2 секунды отбивал бы валидные токены
    # сразу после login.
    jwt_audience: str = Field(default="dbos", alias="JWT_AUDIENCE")
    jwt_issuer: str = Field(default="auth_service", alias="JWT_ISSUER")
    jwt_leeway_seconds: int = Field(default=10, alias="JWT_LEEWAY_SECONDS")

    # Shared secret для service-to-service вызовов (introspect, service-access).
    # Другие сервисы шлют его в `Authorization: Bearer <key>`.
    service_api_key: str = Field(default="change-me-service-key", alias="SERVICE_API_KEY")

    # Per-service API keys (dual-mode со shared `SERVICE_API_KEY`).
    #
    # Если задан как JSON dict `{"loging_service": "<key>", "server_service":
    # "<key>"}` и непустой — `require_service_token` сверяет header
    # `X-Service-Identity` с этим словарём и валидирует Bearer-токен под
    # конкретный ключ. Если пустой/None — fallback на legacy
    # `SERVICE_API_KEY` (один общий секрет на всех caller'ов).
    #
    # Симметрично с loging_service. Полная миграция — отдельным шагом, пока
    # держим оба пути включёнными.
    service_api_keys: dict[str, str] = Field(
        default_factory=dict,
        alias="SERVICE_API_KEYS",
        description=(
            "JSON-словарь {service_name: api_key}. Если непустой — "
            "lookup по X-Service-Identity заменяет проверку shared "
            "SERVICE_API_KEY."
        ),
    )

    @field_validator("service_api_keys", mode="before")
    @classmethod
    def _parse_service_api_keys(cls, v):
        """Принимает либо native dict, либо JSON-строку из env."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return {}
            import json
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return {str(k): str(val) for k, val in parsed.items()}
            return {}
        return v

    # Strict-mode toggle для валидации `X-Service-Identity` заголовка.
    #
    # False (default — soft mode): отсутствующий header разрешён (backward-
    # compat с caller'ами, которые ещё не шлют его); невалидный (значение не
    # из `KNOWN_SERVICE_IDENTITIES`) — WARNING в лог, но запрос проходит.
    #
    # True (strict): header есть, но не в allow-list → 401
    # `INVALID_SERVICE_IDENTITY`. Включать только после того, как все
    # внутренние caller'ы проаудированы (иначе мгновенно ломаем service mesh).
    #
    # Отсутствие header'а **никогда** не reject'ится — это сломает caller'ов
    # из старых релизов, которые о нём не знают. Полная auth-side валидация
    # делается через per-service API keys (`SERVICE_API_KEYS` JSON env);
    # этот toggle — временный defence-in-depth.
    strict_service_identity: bool = Field(default=False, alias="STRICT_SERVICE_IDENTITY")

    # Запретить fallback на legacy shared `SERVICE_API_KEY`, когда задан
    # непустой `SERVICE_API_KEYS`. При True — единственный валидный путь
    # service-to-service auth — per-service ключ через `X-Service-Identity`
    # + соответствующий Bearer из словаря; legacy путь reject'ится даже если
    # caller прислал валидный `SERVICE_API_KEY`. Включать после миграции
    # всех caller'ов на per-service ключи.
    strict_service_api_keys: bool = Field(default=False, alias="STRICT_SERVICE_API_KEYS")

    # Docker registry token auth
    docker_registry_service: str = Field(default="registry.example.com", alias="DOCKER_REGISTRY_SERVICE")
    docker_registry_issuer: str = Field(default="auth_service", alias="DOCKER_REGISTRY_ISSUER")
    docker_token_ttl_minutes: int = Field(default=5, alias="DOCKER_TOKEN_TTL_MINUTES")
    # RSA private key (PEM) для подписи Docker tokens (RS256).
    # Если не задан — генерируется эфемерный RSA-ключ (токены не переживут рестарт).
    # В production обязателен (production guard в `_validate_production_secrets`).
    # Сгенерить: `openssl genrsa -out docker_signing_key.pem 2048`
    docker_rsa_private_key: str | None = Field(default=None, alias="DOCKER_RSA_PRIVATE_KEY")

    # OAuth2
    oauth_code_ttl_seconds: int = Field(default=300, alias="OAUTH_CODE_TTL_SECONDS")

    # Интеграция с loging_service (опционально — fallback на локальный логгер если не задано)
    logging_service_url: str | None = Field(default=None, alias="LOGGING_SERVICE_URL")
    logging_service_api_key: str | None = Field(default=None, alias="LOGGING_SERVICE_API_KEY")

    # Trusted reverse-proxy IPs (allow-list для X-Forwarded-For / X-Real-IP).
    #
    # Если `request.client.host` НЕ в списке — header игнорируется и в качестве
    # client-IP берётся прямой `request.client.host`. Это защищает audit-trail
    # и rate-limit-by-IP от подделки заголовков клиентом.
    #
    # Default — пустой список = НЕ доверяем X-Forwarded-For ни от кого
    # (безопасный default; включай явно только если знаешь свои proxy).
    #
    # Формат: список строк IP. Допускается CIDR (например "10.0.0.0/8") —
    # тогда проверяется network-membership. Можно задавать через env:
    # `TRUSTED_PROXY_IPS='["10.0.0.1","192.168.1.0/24"]'` (JSON-формат).
    trusted_proxy_ips: list[str] = Field(default_factory=list, alias="TRUSTED_PROXY_IPS")

    # ── Per-IP rate-limits (slowapi) ────────────────────────────────────────
    # Защищает credential-критичные эндпоинты от brute-force через ротацию
    # username'ов / IP-spoofing. Применяется ДО endpoint-handler'а: 429
    # отвечается без обращения к БД и без `emit("user.login")` audit-amplification.
    #
    # Per-user lockout (5 неудач → 15 минут) уже работает в `auth_service.login`
    # — но он привязан к username'у. Без per-IP лимита атакующий, перебирающий
    # учётки, обходит lockout, т.к. каждая попытка идёт по новому ключу.
    #
    # Синтаксис slowapi: `'<count>/<period>'`, period ∈ second|minute|hour|day.
    # Use `'0/second'` чтобы фактически выключить лимит для конкретного маршрута
    # (например, в тестах) — мы используем нулевые env-overrides только в
    # devcontainer'е, где per-IP не имеет смысла (все запросы 127.0.0.1).
    login_rate_limit: str = Field(
        default="10/minute",
        alias="LOGIN_RATE_LIMIT",
        description=(
            "Per-IP лимит для `POST /api/auth/v1/login`. Default 10/minute — "
            "стандартная защита от credential stuffing. Argon2id verify ~100ms "
            "CPU, без лимита атакующий выжигает ядра."
        ),
    )
    refresh_rate_limit: str = Field(
        default="30/minute",
        alias="REFRESH_RATE_LIMIT",
        description=(
            "Per-IP лимит для `POST /api/auth/v1/refresh`. Default 30/minute — "
            "legitимный single-tab refresh раз в 9-10 минут, multi-tab клиенты "
            "(SPA, mobile с background refresh) укладываются в 30/min."
        ),
    )
    docker_token_rate_limit: str = Field(
        default="30/minute",
        alias="DOCKER_TOKEN_RATE_LIMIT",
        description=(
            "Per-IP лимит для `GET /api/auth/v1/docker/token`. Default 30/minute "
            "— `docker pull/push` под нагрузкой шлёт burst'ы (один pull = 1-N "
            "token endpoint calls). Lockout по username работает, но "
            "атакующий, ротирующий username'ы, обходит его."
        ),
    )
    introspect_rate_limit: str = Field(
        default="60/minute",
        alias="INTROSPECT_RATE_LIMIT",
        description=(
            "Per-IP лимит для `POST /api/auth/v1/authorization/introspect`. "
            "Default 60/minute — M2M-вызов часто, но не безудержно. "
            "Шлю через middleware так же, как /login."
        ),
    )

    # slowapi backend для счётчиков rate-limit'а.
    #
    # `None`/пусто → fallback на `memory://` (in-memory счётчик per-process).
    # Для dev/test это норм, но в K8s с 2+ репликами каждый pod держит свой
    # счётчик: brute-force получает фактически N × `LOGIN_RATE_LIMIT` попыток,
    # round-robin ingress раскладывает их по pod'ам и per-IP лимит обходится.
    #
    # Для production укажи общий backend: `redis://host:6379/0`,
    # `redis+unix:///var/run/redis.sock`, `memcached://host:11211` и т.д.
    # Полный список схем — в документации `limits` (storage backends).
    #
    # Production-guard ниже не делает это поле обязательным (для совместимости
    # с однопроцессным staging'ом), но `main.py` пишет WARNING на старте, если
    # в prod выбран `memory://`.
    rate_limit_storage_uri: str | None = Field(
        default=None,
        alias="RATE_LIMIT_STORAGE_URI",
        description=(
            "slowapi storage backend (например `redis://host:6379/0`). "
            "Если не задан — fallback на `memory://` (dev/test only; "
            "в K8s с 2+ репликами лимит обходится round-robin'ом)."
        ),
    )

    # ── CORS / security headers ─────────────────────────────────────────────
    cors_allowed_origins: list[str] = Field(
        default_factory=list,
        alias="CORS_ALLOWED_ORIGINS",
        description=(
            "Список allow-origin для CORS. Default пустой = CORS-middleware "
            "не подключается (закрытый API). Для UI задавай явно: "
            "`['https://app.example.com']`."
        ),
    )
    security_hsts_enabled: bool = Field(
        default=False,
        alias="SECURITY_HSTS_ENABLED",
        description=(
            "Включает `Strict-Transport-Security`. Только за https-фронтом — "
            "иначе HTTP-клиенты получают header и ломаются на rebound'е."
        ),
    )
    docker_cert_validity_days: int = Field(
        default=730,
        alias="DOCKER_CERT_VALIDITY_DAYS",
        description=(
            "Срок жизни self-signed cert'а в `get_public_key_pem`. Используется "
            "Docker registry для извлечения public key. 2 года по умолчанию."
        ),
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        """Принимает comma-separated string, JSON-list или native list."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json
                try:
                    parsed = json.loads(v)
                except json.JSONDecodeError:
                    return [v]
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
                return [str(parsed).strip()]
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def _parse_trusted_proxy_ips(cls, v):
        """Поддерживает comma-separated string и JSON-list в дополнение к нативному list."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json
                try:
                    parsed = json.loads(v)
                except json.JSONDecodeError:
                    return [v]
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
                return [str(parsed).strip()]
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    # Bootstrap account_admin (применяется только если таблица users пуста)
    initial_admin_username: str | None = Field(default=None, alias="INITIAL_ADMIN_USERNAME")
    initial_admin_password: str | None = Field(default=None, alias="INITIAL_ADMIN_PASSWORD")
    initial_admin_email: str | None = Field(default=None, alias="INITIAL_ADMIN_EMAIL")

    # ── Production-валидатор ─────────────────────────────────────────────────
    #
    # Запускается только при `APP_ENV=production`. Цель — не дать сервису
    # подняться с дефолтными/слабыми секретами в проде. Без этого guard'а:
    #
    # - `SECRET_KEY="change-me-to-a-long-random-secret-at-least-32-chars"`
    #   (наш `.env.example:33`) спокойно подписывает JWT в проде — токены
    #   подделываются любым, кто прочитал репозиторий.
    # - Bootstrap-админ создаётся с `INITIAL_ADMIN_PASSWORD=change-me-immediately`
    #   (тоже из `.env.example:81`) — атакующий получает `account_admin` мгновенно.
    # - Пустой `LOGGING_SERVICE_API_KEY` → `audit_client.emit()` тихо дропает
    #   события (graceful-drop в `audit_service`), audit-trail разорван.
    # - `SERVICE_API_KEY` остаётся дефолтным → cross-service introspect
    #   обходится `Bearer change-me-service-key`.
    #
    # Ограничения — минимально жёсткие: запрет известных placeholder'ов +
    # length-check там, где длина критична для криптостойкости (HS256 требует
    # ≥ 32 байт ключа, OWASP-минимум для admin-пароля — 12 символов).

    # Известные placeholder-подстроки, которые нельзя оставлять в проде.
    # Substring-match `change-me` ловит весь класс наших дефолтов
    # (`change-me`, `change-me-to-a-long-...`, `change-me-immediately`,
    # `change-me-service-key`) — даже если кто-то «удлинит» строку, забыв
    # заменить её настоящим секретом.
    _WEAK_SECRET_SUBSTRINGS: tuple[str, ...] = ("change-me", "changeme")
    # Точное совпадение для admin-паролей: типичные слабые/placeholder.
    _WEAK_PASSWORD_VALUES: frozenset[str] = frozenset({
        "", "change-me", "changeme", "1234", "12345", "123456",
        "password", "admin", "admin1234",
    })

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Запретить дефолтные секреты в проде. Запускается после загрузки всех полей."""
        if self.app_env != "production":
            return self

        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")

        # ── SECRET_KEY ────────────────────────────────────────────────────
        secret_key = self.secret_key or ""
        if not secret_key:
            raise ValueError("SECRET_KEY must be set in production")
        if any(weak in secret_key.lower() for weak in self._WEAK_SECRET_SUBSTRINGS):
            raise ValueError(
                "SECRET_KEY must be changed in production "
                "(contains placeholder 'change-me')"
            )
        if len(secret_key) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters in production "
                "(generate: python -c \"import secrets; print(secrets.token_hex(32))\")"
            )

        # ── SERVICE_API_KEY ───────────────────────────────────────────────
        service_api_key = self.service_api_key or ""
        if not service_api_key:
            raise ValueError("SERVICE_API_KEY must be set in production")
        if any(weak in service_api_key.lower() for weak in self._WEAK_SECRET_SUBSTRINGS):
            raise ValueError(
                "SERVICE_API_KEY must be changed in production "
                "(contains placeholder 'change-me')"
            )
        if len(service_api_key) < 32:
            raise ValueError(
                "SERVICE_API_KEY must be at least 32 characters in production"
            )

        # ── INITIAL_ADMIN_PASSWORD (только если bootstrap включён) ─────────
        # Bootstrap-админ создаётся, если выставлены username+password И
        # таблица users пуста (см. `services/bootstrap_service.py`). Если
        # username не задан — bootstrap молча skip'ится, проверять нечего.
        if self.initial_admin_username:
            admin_pw = self.initial_admin_password or ""
            if admin_pw.lower() in self._WEAK_PASSWORD_VALUES:
                raise ValueError(
                    "INITIAL_ADMIN_PASSWORD must be set to a non-default value "
                    "when INITIAL_ADMIN_USERNAME is configured in production"
                )
            if any(weak in admin_pw.lower() for weak in self._WEAK_SECRET_SUBSTRINGS):
                raise ValueError(
                    "INITIAL_ADMIN_PASSWORD must be changed in production "
                    "(contains placeholder 'change-me')"
                )
            if len(admin_pw) < 12:
                raise ValueError(
                    "INITIAL_ADMIN_PASSWORD must be at least 12 characters in production"
                )

        # ── LOGGING_SERVICE_API_KEY (обязателен для audit-канала в prod) ──
        # Без ключа `audit_client.emit()` тихо дропает события (graceful
        # no-op в `services/audit_service.py`) или шлёт PAT worker'а
        # fallback'ом — оба пути ломают audit-trail. В проде это
        # недопустимо, требуем непустой ключ.
        if not self.logging_service_api_key:
            raise ValueError(
                "LOGGING_SERVICE_API_KEY must be set in production "
                "(audit channel will silently drop events otherwise)"
            )

        # ── SECURITY_HSTS_ENABLED (warn-only — не error) ───────────────────
        # HSTS не обязателен (за http-фронтом ставить нельзя), но в типовой
        # prod-схеме с https-ingress должен быть включён. Молчаливый дефолт
        # `False` уходил в SIEM без сигнала, теперь хотя бы WARN при старте.
        if not self.security_hsts_enabled:
            logger.warning(
                "SECURITY_HSTS_ENABLED=false in production: HSTS header не "
                "ставится. Если фронт-ingress терминирует https, включи "
                "SECURITY_HSTS_ENABLED=true."
            )

        # docker_jwt: эфемерный RSA-ключ (regenerated на каждом рестарте)
        # ломает Docker registry rootcertbundle — pull/push возвращают 401
        # без видимой причины. В production требуем явно настроенный ключ.
        if not self.docker_rsa_private_key:
            raise ValueError("DOCKER_RSA_PRIVATE_KEY required in production")

        # ── LOGGING_SERVICE_URL https-only ──────────────────────────────────
        # audit-канал по plain http внутри кластера снифается/перехватывается
        # — атакующий с network-namespace доступом подменит ack/тихо съест
        # события (события про bans/role-changes/login-failures пропадут из
        # SIEM). Зеркалит симметричный гард в loging_service для
        # AUTH_SERVICE_URL. Localhost-исключение для devcontainer/sidecar.
        if self.logging_service_url:
            parsed = urlparse(self.logging_service_url)
            scheme = (parsed.scheme or "").lower()
            host = (parsed.hostname or "").lower()
            if scheme == "http" and host not in _LOCAL_HOSTS:
                raise ValueError(
                    "LOGGING_SERVICE_URL must use https:// in production "
                    f"(got scheme={scheme!r}, host={host!r}); plain http "
                    "exposes the audit channel to MITM/sniff in the cluster"
                )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Закэшированные application settings (один инстанс на процесс)."""
    return Settings()
