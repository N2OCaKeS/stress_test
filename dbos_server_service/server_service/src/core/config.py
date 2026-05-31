"""Настройки приложения. Все через env (pydantic-settings)."""

import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password
# (`redis://redis:6379/0`) и без user:pass-сегмента вообще — не матчит.
# Симметричен с `server_worker/src/core/config.py:_REDIS_URL_PASSWORD_RE`.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Hosts, для которых разрешён plain http в prod/staging (devcontainer /
# on-host port-forward, где TLS терминируется на той же машине и MITM-модель
# выглядит иначе). Симметрично `loging_service/src/core/config.py:_LOCAL_HOSTS`.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    """Контейнер настроек. Поля грузятся из env (case-insensitive) и из .env-файла."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(
        default="local",
        description=(
            "Метка окружения деплоя: local|dev|test|staging|production. "
            "В 'production' публичный OpenAPI surface (/openapi.json, /docs, /redoc) "
            "закрыт, чтобы анонимы не видели каталог эндпоинтов (включая stub-summary'и)."
        ),
    )
    database_url: str = Field(
        ...,
        description="PostgreSQL async DSN, например postgresql+psycopg://user:pass@host/db",
    )
    # SQLAlchemy connection pool sizing. Дефолты под dev/test-стенд:
    # 10 постоянных + 20 burst — хватает 4 uvicorn-воркерам, не упирается
    # в `max_connections` локального postgres. В прод-нагрузке поднимать
    # вслед за БД-конфигом (`pgbouncer pool_size`). Симметрично с
    # `loging_service` и `server_worker`.
    db_pool_size: int = Field(default=10, ge=1, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, alias="DB_MAX_OVERFLOW")
    auth_service_url: str = Field(
        ...,
        description="Base URL auth_service для introspect-вызовов, например http://auth_service:8000",
    )
    auth_request_timeout_seconds: float = Field(
        default=3.0,
        description="HTTP-таймаут для вызовов в auth_service",
    )
    introspect_pool_max_connections: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices(
            "AUTH_POOL_MAX_CONNECTIONS",
            "INTROSPECT_POOL_MAX_CONNECTIONS",
            "introspect_pool_max_connections",
        ),
        description=(
            "Верхняя граница TCP-соединений для pooled httpx-клиентов "
            "auth_service (`_introspect_client` + `_auth_client`). Под "
            "slowloris-burst пул не разрастается, ставит back-pressure на "
            "дальнейшие introspect/proxy запросы. Слишком низкий лимит → "
            "ложные 503 на пике, слишком высокий → FD-пул процесса исчерпан. "
            "20 — sane default для одного uvicorn worker'а. Env: "
            "`AUTH_POOL_MAX_CONNECTIONS` (предпочтительно) или legacy "
            "`INTROSPECT_POOL_MAX_CONNECTIONS`."
        ),
    )
    introspect_pool_max_keepalive: int = Field(
        default=10,
        ge=0,
        validation_alias=AliasChoices(
            "AUTH_POOL_MAX_KEEPALIVE",
            "INTROSPECT_POOL_MAX_KEEPALIVE",
            "introspect_pool_max_keepalive",
        ),
        description=(
            "Сколько idle keep-alive соединений httpx-пул держит открытыми. "
            "Должно быть <= introspect_pool_max_connections. 0 отключает "
            "keep-alive (каждый запрос — новый TCP+TLS handshake). 10 — баланс "
            "между амортизацией handshake'а и idle-FD. Env: "
            "`AUTH_POOL_MAX_KEEPALIVE` (предпочтительно) или legacy "
            "`INTROSPECT_POOL_MAX_KEEPALIVE`."
        ),
    )
    service_api_key: str = Field(
        default="",
        description=(
            "Shared service-to-service secret. Отправляется как 'Authorization: Bearer <key>' "
            "при вызовах auth_service /authorization/introspect (и /service-access). "
            "Должен совпадать с SERVICE_API_KEY env у auth_service. Пустое значение — dev-only, "
            "auth_service отобьёт вызов 401."
        ),
    )
    server_encryption_key: str = Field(
        ...,
        min_length=32,
        description=(
            "Мастер-ключ для симметричного шифрования хранимых паролей (server "
            "accounts, IPMI credentials). Активный wire-формат (v2+) выводит AES-ключ "
            "через HKDF-SHA256, так что структурно подойдёт любая строка, но "
            "min_length жмёт оператора на нетривиальную энтропию. Сгенерить через "
            "`openssl rand -base64 32` (или длиннее)."
        ),
    )
    server_encryption_key_version: int = Field(
        default=2,
        ge=2,
        description=(
            "Активная версия ключа, которая пишется в новые ciphertext'ы (формат `v<N>$...`). "
            "Версия 1 использовала legacy одношаговый SHA-256 и оставлена только для "
            "расшифровки старых ciphertext'ов; версия >= 2 — HKDF-SHA256. Запрет на v1 "
            "касается только НОВОЙ записи: legacy v1-ciphertext'ы расшифровываются по "
            "версии из их префикса (ключи SERVER_ENCRYPTION_KEY__vN остаются доступны)."
        ),
    )
    hkdf_salt_hex: str = Field(
        default="",
        description=(
            "HKDF-salt в hex для деривации AES-ключа из `SERVER_ENCRYPTION_KEY` "
            "(v2+). Минимум 32 hex-символа (16 байт). В production/staging "
            "ОБЯЗАТЕЛЕН — Settings не стартует с пустым значением, чтобы два "
            "независимых деплоя не получили одинаковый KDF-output и общий "
            "ciphertext domain. В dev/test/local пустое значение допустимо: "
            "secrets_service подставляет фиксированный fallback (`_FALLBACK_HKDF_SALT`). "
            "Сгенерить: `python -c \"import secrets; print(secrets.token_hex(32))\"`."
        ),
    )
    server_worker_database_url: str = Field(
        default="",
        description="PostgreSQL DSN базы server_worker (для INSERT в tasks). Пусто = dispatch отключён.",
    )
    server_worker_redis_url: str = Field(
        default="",
        description="Redis URL, используемый как taskiq broker для публикации задач server_worker'у. Пусто = dispatch отключён.",
    )
    prepare_creds_ttl_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Время жизни bootstrap-кред в Redis при dispatch `server.prepare`. "
            "Креды кладутся под ключ `dbos:prepare_creds:<task_id>` с этим TTL, "
            "в task-payload едет только ссылка на ключ. Воркер читает креды на "
            "каждой попытке, пока ключ жив (TTL подстраховывает retry и чистит "
            "креды без явного удаления). 15 минут — запас на несколько retry с "
            "back-off, после чего повторный prepare требует заново прислать креды."
        ),
    )
    logging_service_url: str = Field(
        default="",
        description=(
            "Base URL loging_service для отправки audit-событий, например "
            "http://loging_service:8000. Пусто отключает удалённый аудит "
            "(события тогда логгируются локально через 'audit' logger)."
        ),
    )
    logging_service_api_key: str = Field(
        default="",
        description=(
            "Shared service-to-service secret. Отправляется как 'Authorization: Bearer <key>' "
            "при вызовах loging_service /api/logging/v1/events. Должен совпадать с "
            "LOGING_SERVICE_API_KEY env у loging_service. Пусто отключает удалённый аудит."
        ),
    )
    global_rate_limit: str = Field(
        default="500/minute",
        description=(
            "Глобальный per-IP rate-limit на все эндпоинты (синтаксис slowapi: "
            "'<count>/<period>', где period ∈ second|minute|hour|day). "
            "Защищает 401-pipeline от slowloris: атакующий с trash-shape "
            "Bearer-токенами отбивается bearer-shape pre-check'ом в "
            "`dependencies/auth.py`, но без rate-limit'а может бомбить "
            "сервис по CPU/log/audit-emit. Health endpoints (/health, "
            "/ready) исключены из ограничения."
        ),
    )
    os_versions_anon_rate_limit: str = Field(
        default="100/minute",
        alias="OS_VERSIONS_ANON_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на анонимные GET /os-versions* (синтаксис "
            "slowapi). Каталог публичный — без этого лимита анонимный сканер "
            "выкачивает имена/версии и шумит в audit "
            "`os_version.list_anonymous`/`view_anonymous`. Лимит применяется "
            "поверх `global_rate_limit`; authenticated read под глобальным."
        ),
    )
    ipmi_verify_max_age_seconds: int = Field(
        default=60,
        ge=1,
        alias="IPMI_VERIFY_MAX_AGE_SECONDS",
        description=(
            "Максимальный возраст `verified_at` в "
            "`IpmiCredentialsRotatedRequest` (секунды). Worker обязан "
            "подтвердить BMC test-call в этом окне после apply; иначе "
            "приёмник отбивает 400 BMC_VERIFY_REQUIRED. По умолчанию 60s — "
            "достаточно для round-trip apply→verify→storage, не даёт "
            "реиспользовать давний successful test."
        ),
    )
    ipmi_rotate_per_server_rate_limit: str = Field(
        default="5/minute",
        alias="IPMI_ROTATE_PER_SERVER_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на dispatch ротации IPMI-credentials через "
            "POST /ipmi-controllers/{id}/rotate (синтаксис slowapi). Ротация "
            "запускает BMC-операцию + worker-task — частый burst грозит "
            "DoS'нуть BMC и забить очередь. Применяется поверх "
            "`global_rate_limit`."
        ),
    )
    server_prepare_rate_limit: str = Field(
        default="3/minute",
        alias="SERVER_PREPARE_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на bootstrap-управления через POST "
            "/servers/{id}/prepare (синтаксис slowapi). Каждый dispatch "
            "пишет plaintext-creds в Redis под TTL и поднимает worker-task, "
            "поэтому burst либо забивает Redis сиротскими ключами, либо "
            "перегружает worker. Применяется поверх `global_rate_limit`."
        ),
    )
    ipmi_credentials_rotate_rate_limit: str = Field(
        default="5/minute",
        alias="IPMI_CREDENTIALS_ROTATE_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на пользовательский POST "
            "/servers/{id}/ipmi/credentials/rotate — прямая ротация без worker'а "
            "(админ применил пароль вручную/через CLI и записывает ciphertext). "
            "CRITICAL-аудит на каждый вызов; burst грозит шумом и race'ом с "
            "BMC. Применяется поверх `global_rate_limit`."
        ),
    )
    account_rotate_password_rate_limit: str = Field(
        default="10/minute",
        alias="ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на POST /server-accounts/{id}/rotate_password — "
            "user-initiated ротация общего ciphertext'а без SSH-apply. CRITICAL-"
            "аудит; burst грозит шумом и парой race'ов с параллельным "
            "/rotate-dispatch'ем. Применяется поверх `global_rate_limit`."
        ),
    )
    security_hsts_enabled: bool = Field(
        default=False,
        alias="SECURITY_HSTS_ENABLED",
        description=(
            "Включает `Strict-Transport-Security` на всех ответах. Только за "
            "https-фронтом — иначе HTTP-клиенты получают header и ломаются на "
            "rebound'е. Прочие защитные заголовки (X-Frame-Options, CSP, "
            "X-Content-Type-Options, Referrer-Policy) ставятся всегда."
        ),
    )
    internal_require_dept_header: bool = Field(
        default=True,
        description=(
            "True (default, secure-by-default) — /api/server/v1/internal/* "
            "endpoints требуют от caller'а (server_worker) заголовок "
            "`X-Target-Department-Id`, совпадающий с реальным "
            "server.department_id. Mismatch / отсутствие → 403. "
            "False — header читается только для cross-check'а в audit; "
            "mismatch логгируется, но не блокирует. Soft mode оставлен только "
            "для dev/test (conftest при необходимости выставляет False явно) — "
            "в любом deploy'е, который не контролирует worker-PAT периметр, "
            "должно быть True."
        ),
    )

    # Доверенные IP reverse-proxy (allow-list для X-Forwarded-For / X-Real-IP).
    #
    # Если `request.client.host` НЕ в списке — header игнорируется и в качестве
    # client-IP берётся прямой `request.client.host`. Это защищает audit-trail
    # (ip_address актора) и rate-limit-by-IP от подделки заголовков клиентом —
    # без allow-list любой запрос с `X-Forwarded-For: 10.20.30.40` мог фреймить
    # оператора на дашборде incident-response.
    #
    # Default — пустой список = НЕ доверяем X-Forwarded-For ни от кого
    # (безопасный default; включай явно только если знаешь свои proxy).
    #
    # Формат: список строк IP. Допускается CIDR (например "10.0.0.0/8") —
    # тогда проверяется network-membership. Через env: comma-separated
    # ("10.0.0.1,192.168.1.0/24") или JSON-формат
    # `TRUSTED_PROXY_IPS='["10.0.0.1","192.168.1.0/24"]'`.
    trusted_proxy_ips: list[str] = Field(
        default_factory=list, alias="TRUSTED_PROXY_IPS"
    )

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

    @model_validator(mode="after")
    def _check_introspect_pool_sanity(self) -> "Settings":
        """keepalive > max_connections — невалидная конфигурация httpx-пула."""
        if self.introspect_pool_max_keepalive > self.introspect_pool_max_connections:
            raise ValueError(
                "AUTH_POOL_MAX_KEEPALIVE / INTROSPECT_POOL_MAX_KEEPALIVE "
                f"({self.introspect_pool_max_keepalive}) cannot exceed "
                "AUTH_POOL_MAX_CONNECTIONS / INTROSPECT_POOL_MAX_CONNECTIONS "
                f"({self.introspect_pool_max_connections})"
            )
        return self

    @field_validator("hkdf_salt_hex", mode="after")
    @classmethod
    def _validate_hkdf_salt_format(cls, v: str) -> str:
        """Если salt задан (непустой) — он обязан быть валидным hex'ом >= 32 символов.

        Пустое значение допускается на этом уровне; `_require_hkdf_salt_in_prod`
        отдельно отбивает пустоту в production/staging. 32 hex = 16 байт —
        нижняя граница для HKDF-salt по RFC 5869 рекомендациям.
        """
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
            raise ValueError(
                f"HKDF_SALT_HEX must be valid hex: {exc}"
            ) from exc
        return v

    @model_validator(mode="after")
    def _require_hkdf_salt_in_prod(self) -> "Settings":
        """В production/staging `HKDF_SALT_HEX` обязателен.

        Без явной salt секрет деривируется через fallback-константу — два
        независимых деплоя получают идентичный KDF-output, что нарушает
        изоляцию ciphertext domain'ов между средами. Symmetric с
        `_require_redis_auth_in_prod`: dev/test разрешает пустоту, прод
        — нет.
        """
        if self.app_env.lower() in {"production", "staging"} and not self.hkdf_salt_hex:
            raise ValueError(
                f"HKDF_SALT_HEX must be set in {self.app_env} "
                "(min 32 hex chars). Generate: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        return self

    @model_validator(mode="after")
    def _require_redis_auth_in_prod(self) -> "Settings":
        """В production/staging `SERVER_WORKER_REDIS_URL` обязан содержать password.

        Зеркало `server_worker/src/core/config.py:_require_redis_auth_in_prod`.
        Без AUTH'а любой контейнер в той же k8s/Docker-сети сможет `RPUSH` в
        taskiq-очередь и инициировать power-cycle/password-rotation от имени
        worker'а (privilege escalation, RCE-вектор).

        Mirror логика worker'а: server_service — publisher, worker —
        consumer; обоим нужен strict-AUTH в проде на ОДНОЙ и той же
        Redis-DB.

        Пустой `server_worker_redis_url` (dispatch отключён) разрешён в
        любом окружении — это намеренный fail-closed default для
        environment'ов без worker'а (legacy/dev/test).
        """
        if self.app_env.lower() in {"production", "staging"} and self.server_worker_redis_url:
            if not _REDIS_URL_PASSWORD_RE.search(self.server_worker_redis_url):
                raise ValueError(
                    "SERVER_WORKER_REDIS_URL must contain a password in "
                    f"{self.app_env} (format: redis://:<password>@host:port/db). "
                    "Anonymous Redis exposes the taskiq queue to RPUSH from "
                    "any co-located container."
                )
        return self

    @model_validator(mode="after")
    def _require_min_key_version_in_prod(self) -> "Settings":
        """В production/staging активная версия ключа обязана быть >= 2.

        Версия 1 — legacy одношаговый SHA-256 без HKDF-salt и без итераций.
        Поле `server_encryption_key_version` уже ограничено `ge=2` на уровне
        Field, так что v1 не пройдёт ни в одном окружении; этот guard —
        defense-in-depth для prod/staging с явным сообщением. Расшифровка
        старых v1-ciphertext'ов при этом сохраняется: их версия читается из
        префикса самого ciphertext'а, а не из этого поля.
        """
        if self.app_env.lower() in {"production", "staging"} and self.server_encryption_key_version < 2:
            raise ValueError(
                "SERVER_ENCRYPTION_KEY_VERSION must be >= 2 in "
                f"{self.app_env} (v1 uses legacy SHA-256 without HKDF; "
                "new writes under v1 are forbidden)"
            )
        return self

    @model_validator(mode="after")
    def _require_service_api_key_in_prod(self) -> "Settings":
        """В production/staging `SERVICE_API_KEY` обязан быть непустым.

        Пустой ключ → introspect-вызовы уходят с `Authorization: Bearer `
        (пустой bearer), auth_service отвечает 401, и сервис деградирует до
        вечных 503 — но это всплывает только в рантайме. Ловим на старте,
        симметрично `_require_redis_auth_in_prod`. В dev/test/local пустой
        ключ допустим (introspect замокан / auth_service не используется).
        """
        if self.app_env.lower() in {"production", "staging"} and not self.service_api_key:
            raise ValueError(
                f"SERVICE_API_KEY must be set in {self.app_env}; "
                "an empty key makes every introspect call to auth_service "
                "fail with 401"
            )
        return self

    @model_validator(mode="after")
    def _require_https_logging_url_in_prod(self) -> "Settings":
        """В production/staging `LOGGING_SERVICE_URL` должен быть https://.

        Зеркало `_require_https_auth_url_in_prod`. Audit-события несут
        actor_id/username/IP/action/department_id — на plain http в кластере
        любой sniff/MITM в network namespace перехватит security-чувствительные
        события (rotate_password, view_credentials, power_on, ban) или подменит
        их. Localhost — исключение для devcontainer / port-forward. Пустой URL
        (удалённый аудит отключён) проверку пропускает.
        """
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
                f"{self.app_env} (got scheme={scheme!r}, host={host!r}); "
                "audit events carry actor PII and security-sensitive data"
            )
        return self

    @model_validator(mode="after")
    def _require_https_auth_url_in_prod(self) -> "Settings":
        """В production/staging `AUTH_SERVICE_URL` должен быть https://.

        Симметрия с `loging_service/src/core/config.py:_validate_production_secrets`.
        Introspect-запросы несут чужие bearer'ы пользователя — на plain http
        в кластере любой sniff/MITM в network namespace перехватит токены
        или подменит ответ (`{active: true, platform_role: "account_admin"}`)
        → произвольный admin-доступ. Localhost — исключение для
        devcontainer / port-forward сценариев.
        """
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        parsed = urlparse(self.auth_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and host not in _LOCAL_HOSTS:
            raise ValueError(
                "AUTH_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r}); "
                "plain http exposes introspect to MITM/sniff in the cluster"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Кэшированный фабричный метод. Один Settings на процесс."""
    return Settings()
