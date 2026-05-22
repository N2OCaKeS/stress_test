"""Конфигурация приложения."""

import json
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


_DEFAULT_SERVICE_API_KEY = "change-me-service-key"

# Hostnames, где plain `http://` допустим даже в production. Используется
# https-only гардом для `AUTH_SERVICE_URL` ниже — devcontainer / sidecar /
# on-host debug-сценарии, где TLS терминируется на localhost.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="loging_service", alias="APP_NAME")
    app_env: Literal["local", "development", "test", "production"] = Field(
        default="local", alias="APP_ENV"
    )
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8001, alias="APP_PORT")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://logging_user:logging_password@localhost:5432/logging_db",
        alias="DATABASE_URL",
    )

    # Shared secret, который другие сервисы шлют в `Authorization: Bearer <key>`.
    # FALLBACK когда `service_api_keys` пуст — сохраняет backward-compat с
    # старой single-key моделью деплоя.
    service_api_key: str = Field(default=_DEFAULT_SERVICE_API_KEY, alias="SERVICE_API_KEY")

    # Per-service API keys. Закрывают «компрометация shared-ключа → forge
    # audit от имени любого сервиса» — `X-Service-Identity` allow-list сам
    # этого не делает (header не аутентифицирован, любой держатель
    # `SERVICE_API_KEY` заявит любую identity).
    #
    # Формат: JSON-объект service-identity → bearer-secret, напр.::
    #
    #     SERVICE_API_KEYS='{"auth_service":"k1","server_service":"k2"}'
    #
    # Тип поля `str | dict[str, str]` (не голый `dict[str, str]`), чтобы
    # `pydantic-settings` не парсил env как JSON раньше нашего валидатора —
    # auto-парсер падает с `SettingsError` без нашего точного сообщения.
    #
    # Порядок резолва в `require_service_token`:
    #   1. `service_api_keys` непустой — authoritative. Header
    #      `X-Service-Identity` обязателен, обязан быть в map'е — иначе
    #      401. `compare_digest(provided, keys[identity])` решает
    #      accept/reject. Map — заодно эффективный allow-list (identity
    #      вне map отвергается, даже если она в `KNOWN_SERVICE_IDENTITIES`).
    #   2. `service_api_keys` пуст (legacy single-key) — fallback на
    #      `service_api_key` + старый soft-mode allow-list для
    #      `X-Service-Identity`.
    #
    # Значения — bearer-secret as-is (не хеш), чтобы ротация делалась
    # тем же kubectl-rollout'ом, что и `SERVICE_API_KEY`.
    service_api_keys: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, alias="SERVICE_API_KEYS"
    )

    # URL auth_service'а для валидации `loging_admin` JWT.
    auth_service_url: str | None = Field(default=None, alias="AUTH_SERVICE_URL")

    # Outbound bearer для POST /introspect в auth_service. По умолчанию пуст —
    # тогда `_fetch_identity` фолбэчит на `service_api_key` (legacy / single-key
    # стенды). На раздельных деплоях (`SERVICE_API_KEYS` per-service) выдай
    # этому полю свой ключ — компрометация ingest-ключа другого сервиса
    # не позволит читать чужие introspect-ответы от имени loging_service.
    introspect_service_api_key: str = Field(
        default="", alias="INTROSPECT_SERVICE_API_KEY"
    )

    # Таймаут pooled introspect HTTP-вызова (и per-call fallback'а, который
    # используют тесты). 3 секунды — исторический default из старого синхронного
    # `httpx.post(timeout=3.0)`. Connect-таймаут на pooled клиенте — 2 секунды
    # (см. `main.lifespan`), чтобы залипший TCP-handshake с auth_service падал
    # быстрее, не держа pool-slot.
    introspect_timeout_seconds: float = Field(
        default=3.0, alias="INTROSPECT_TIMEOUT_SECONDS"
    )

    # Проверяет ли introspect-клиент `httpx.AsyncClient` TLS-сертификат
    # auth_service. Default `True` — ОБЯЗАН оставаться True в production, где
    # `AUTH_SERVICE_URL` — https. Ставить в False допустимо только в
    # devcontainer / local-стеках, где auth_service использует self-signed
    # и мы доверяем loopback-границе.
    #
    # Production guard: `_validate_production_secrets` отвергает
    # `verify=False` при `APP_ENV=production` + `AUTH_SERVICE_URL` — внешний
    # https. Сочетание https + verify=False молча отключает MITM-защиту,
    # которую устанавливает https-гард; закрываем дыру при загрузке конфига,
    # а не разбираемся потом в incident-анализе.
    introspect_tls_verify: bool = Field(
        default=True, alias="INTROSPECT_TLS_VERIFY"
    )

    # Per-IP rate-limit на `POST /events` ingest (синтаксис slowapi: `<n>/<period>`).
    # Quick-win: скомпрометированный shared SERVICE_API_KEY не сможет насытить
    # БД (~3000 ev/s, ~10 GB/h), как только один client IP вылезет за cap.
    # Per-service (per-API-key) keying — отдельно. Health-пробы
    # исключены в `main.py` (см. `_RATE_LIMIT_EXEMPT_PATHS`).
    ingest_rate_limit: str = Field(default="100/minute", alias="INGEST_RATE_LIMIT")

    # Per-service-identity rate-limit на `POST /services/{service}/events`.
    # Симметрия с `INGEST_RATE_LIMIT`, но key'ится по `X-Service-Identity`,
    # а не IP — batch-канал доступен только internal caller'ам через k8s
    # ingress, все запросы с одним IP nginx'а, и per-IP лимит был бы либо
    # too loose, либо самобаном легита. Без этого лимита один
    # скомпрометированный SERVICE_API_KEY дудосит БД: `register_events`
    # принимает каталог из 1000 action'ов одним запросом — 100 RPS = 100k
    # upsert'ов/s. Если header отсутствует (legacy soft), key_func фолбэчит
    # на IP — тот же дефолт, что и `POST /events`.
    register_events_rate_limit: str = Field(
        default="100/minute", alias="REGISTER_EVENTS_RATE_LIMIT"
    )

    # Включить ли `X-RateLimit-Limit/Remaining/Reset` response headers.
    # По умолчанию off: `Remaining` утекает атакующему оставшийся бюджет,
    # и тот burst'ит ровно под лимит. Включай, только если у legit caller'а
    # нужен предсказуемый back-off (на prod пока нет такого требования).
    rate_limit_headers_enabled: bool = Field(
        default=False, alias="RATE_LIMIT_HEADERS_ENABLED"
    )

    # Strict-mode для валидации `X-Service-Identity` на service-token
    # эндпоинтах. Симметрично с auth_service:
    #
    # * default (soft mode, `False`) — отсутствие или unknown identity
    #   логируется как WARNING, но запрос проходит. Позволяет роллауту
    #   ехать без координированного передеплоя всех caller'ов.
    # * strict mode (`True`) — отсутствие identity или identity не в
    #   :data:`KNOWN_SERVICE_IDENTITIES` отвергается с 401
    #   `INVALID_SERVICE_IDENTITY`. Включать только после аудита, что все
    #   внутренние caller'ы шлют известное значение.
    #
    # Независимо от этого переключателя: когда service-token эндпоинт
    # привязан к конкретному сервису через path-параметр
    # (`POST /services/{service}/events`), эндпоинт сравнивает
    # advertised identity с путём — mismatch ВСЕГДА 403
    # `SERVICE_IDENTITY_PATH_MISMATCH`, независимо от strict mode. Soft
    # mode рулит только обработкой *отсутствующих* / *неизвестных* headers.
    strict_service_identity: bool = Field(
        default=False, alias="STRICT_SERVICE_IDENTITY"
    )

    # Hard cap на размер тела запроса для мутирующих методов
    # (POST/PUT/PATCH). Закрывает DoS-вектор, ортогональный
    # `ingest_rate_limit`: один атакующий с SERVICE_API_KEY может прислать
    # 100 MB body — ASGI прочитает в память ДО того, как отработает
    # `EventCreate` валидатор (Pydantic видит уже распарсенный объект).
    # 30 параллельных uploads × 100 MB → воркеры OOM и pgsql-пул
    # исчерпывается ещё до того, как один client упрётся в per-IP лимит.
    #
    # Default 1 MiB — щедро для легитимных событий аудита:
    #   * `details` уже ограничено 64 KiB (`EventCreate._details_size`);
    #   * сумма остальных string-полей < 1 KiB;
    #   * 1 MiB оставляет ~15× запаса на будущий рост схемы.
    # Middleware, который это форсит, живёт в `main.py::limit_body_size`.
    max_request_body_bytes: int = Field(
        default=1024 * 1024, alias="MAX_REQUEST_BODY_BYTES"
    )

    # Запускать ли retention-cleanup loop в lifespan'е. По умолчанию True —
    # production стартует daemon-thread, который раз в сутки в 00:00 MSK
    # применяет активную retention-политику.
    #
    # В тестах отключаем: TestClient прогоняет полный lifespan на каждом
    # фикстуре, а thread держит ссылку на module-level `SessionLocal`,
    # который связан с дефолтным `DATABASE_URL=localhost:5432` (не с
    # `TEST_DATABASE_URL`). Под тестами на MSK hour=0 это спамит
    # `Retention cleanup failed: connection to 127.0.0.1:5432 refused` в
    # каждой test-setup'е. Conftest выставляет `RETENTION_LOOP_ENABLED=False`.
    retention_loop_enabled: bool = Field(
        default=True, alias="RETENTION_LOOP_ENABLED"
    )

    @field_validator("service_api_keys", mode="before")
    @classmethod
    def _parse_service_api_keys(cls, v):
        """Принимает либо dict (программный), либо JSON-строку (env var).

        `pydantic-settings` нативно не умеет распаковывать env-vars в
        вложенный `dict[str, str]` — env-значения это всегда строки. Поэтому
        принимаем JSON-строку объекта на парс-стейдже и приводим к dict;
        caller'ы, передающие настоящий dict (тесты, in-process конфиг),
        проходят без изменений.

        Пустая строка / `None` → пустой dict (совпадает с default), так что
        deploy может явно прислать `SERVICE_API_KEYS=` чтобы сказать
        «используем legacy shared key», не пропуская переменную из манифеста.
        """
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"SERVICE_API_KEYS must be a JSON object string "
                    f"(got JSONDecodeError: {exc.msg})"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "SERVICE_API_KEYS must be a JSON object, "
                    f"got {type(parsed).__name__}"
                )
            # Все значения должны быть строками — silent str-coercion
            # маскировал бы опечатки в конфиге (JSON number → ключ обрежется).
            for k, val in parsed.items():
                if not isinstance(k, str) or not isinstance(val, str):
                    raise ValueError(
                        "SERVICE_API_KEYS entries must be string→string; "
                        f"got {type(k).__name__}→{type(val).__name__}"
                    )
            return parsed
        raise ValueError(
            f"SERVICE_API_KEYS must be JSON object or dict, "
            f"got {type(v).__name__}"
        )

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Запретить default / empty SERVICE_API_KEY в production.

        Симметрично `auth_service::_validate_production_secrets` — default
        shared secret позволил бы любому network-reachable актору
        подделывать события аудита (включая `service='loging_service'` записи,
        которые retention-инвариант никогда не удаляет).
        """
        if self.app_env == "production":
            if not self.service_api_key or self.service_api_key == _DEFAULT_SERVICE_API_KEY:
                raise ValueError(
                    "SERVICE_API_KEY must be changed from the default and non-empty in production"
                )
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in production")

            # Per-service mode (SERVICE_API_KEYS непустой) делает ingest-ключи
            # и introspect-ключ независимыми сущностями. Если `service_api_keys`
            # настроен, а `introspect_service_api_key` пуст — `_fetch_identity`
            # тихо фолбэчит на `service_api_key` (single-shared-key). Деплой
            # выглядит «всё ок», но компрометация одного ingest-ключа открывает
            # чтение introspect-ответов от имени loging_service. Гард ловит
            # эту deployment-trap'у на старте.
            if self.service_api_keys and not self.introspect_service_api_key:
                raise ValueError(
                    "INTROSPECT_SERVICE_API_KEY must be set when SERVICE_API_KEYS "
                    "(per-service mode) is configured in production"
                )

            # ── AUTH_SERVICE_URL https-only гард ─────────────────────────
            # `_fetch_identity` (dependencies/auth.py) POST'ит токен на
            # auth_service через httpx. На plain `http://` внутри кластера
            # любой sniff/MITM в network namespace может подменить ответ
            # `{active: true, platform_role: "loging_admin"}` → атакующий
            # получает admin-доступ ко всему audit-каналу.
            #
            # Исключение для localhost/127.0.0.1/::1 — debug-сценарии
            # (devcontainer, on-host port-forward), где TLS терминируется
            # на той же машине и MITM-модель выглядит иначе.
            if self.auth_service_url:
                parsed = urlparse(self.auth_service_url)
                scheme = (parsed.scheme or "").lower()
                host = (parsed.hostname or "").lower()
                if scheme == "http" and host not in _LOCAL_HOSTS:
                    raise ValueError(
                        "AUTH_SERVICE_URL must use https:// in production "
                        f"(got scheme={scheme!r}, host={host!r}); plain http "
                        "exposes introspect to MITM/sniff in the cluster"
                    )

                # ── INTROSPECT_TLS_VERIFY=False гард ─────────────────────
                # `https://` + verify=False молча отключает chain-of-trust,
                # которую только что поставил https-гард. Атакующий с
                # network-namespace MITM-доступом может предъявить любой
                # сертификат, и introspect-вызов всё равно поверит fake
                # ответу `{active: true, platform_role: "loging_admin"}` →
                # произвольный admin-доступ к audit-каналу. Localhost —
                # исключение (self-signed devcontainer — другая модель угрозы).
                if (
                    scheme == "https"
                    and host not in _LOCAL_HOSTS
                    and not self.introspect_tls_verify
                ):
                    raise ValueError(
                        "INTROSPECT_TLS_VERIFY must be true in production "
                        f"when AUTH_SERVICE_URL is a remote https:// endpoint "
                        f"(got host={host!r}); verify=false on an https remote "
                        "silently disables MITM protection — set "
                        "INTROSPECT_TLS_VERIFY=true and provide a valid CA "
                        "bundle for self-signed certificates"
                    )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
