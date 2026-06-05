"""Конфигурация приложения."""

import json
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    app_env: Literal["local", "development", "test", "staging", "production"] = Field(
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

    # SQLAlchemy connection pool sizing. Дефолты выставлены под dev/test-стенд:
    # 10 постоянных коннектов + 20 burst — хватает 4 uvicorn-воркерам, не
    # передаёт лимит `max_connections` локального postgres. В прод-нагрузке
    # значения нужно поднимать вслед за БД-конфигом (`pgbouncer pool_size`).
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")

    # Per-service API keys — единственный режим service-to-service auth.
    # JSON-объект service-identity → bearer-secret, напр.::
    #
    #     SERVICE_API_KEYS='{"auth_service":"k1","server_service":"k2"}'
    #
    # `require_service_token` требует `X-Service-Identity` header, lookup'ает
    # ключ по identity, и сравнивает `compare_digest`'ом. Identity вне map'а
    # отвергается (map = operator allow-list). Компрометация одного ключа
    # ограничена write-доступом от имени соответствующей identity.
    #
    # Тип поля `Annotated[..., NoDecode]` (не голый `dict[str, str]`), чтобы
    # `pydantic-settings` не парсил env как JSON раньше нашего валидатора —
    # auto-парсер падает с `SettingsError` без нашего точного сообщения.
    #
    # Значения — bearer-secret as-is (не хеш): ротация — обычный
    # kubectl-rollout с новым Secret.
    service_api_keys: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, alias="SERVICE_API_KEYS"
    )

    # URL auth_service'а для валидации `loging_admin` JWT.
    auth_service_url: str | None = Field(default=None, alias="AUTH_SERVICE_URL")

    # Outbound bearer для POST /introspect в auth_service. Должен быть отличным
    # от любого значения `SERVICE_API_KEYS` (см. key-separation guard ниже).
    # В production обязателен; в local/dev допустимо оставить пустым — тогда
    # JWT-защищённые эндпоинты вернут 503 INTROSPECT_NOT_INITIALIZED при
    # первом же запросе (введено в `_fetch_identity`).
    introspect_service_api_key: str = Field(
        default="", alias="INTROSPECT_SERVICE_API_KEY"
    )

    # Таймаут pooled introspect HTTP-вызова (и эфемерного fallback-клиента,
    # который собирается на запрос, если pool=None). 3 секунды — исторический
    # default. Connect-таймаут на pooled клиенте — 2 секунды (см.
    # `main.lifespan`), чтобы залипший TCP-handshake с auth_service падал
    # быстрее, не держа pool-slot.
    introspect_timeout_seconds: float = Field(
        default=3.0, alias="INTROSPECT_TIMEOUT_SECONDS"
    )

    # Отдельный connect-таймаут pooled introspect-клиента. Read/write делит
    # общий `introspect_timeout_seconds`; для TCP+TLS handshake'а нужен
    # более жёсткий бюджет, чтобы залипший FIN-WAIT не отъедал pool-slot
    # на полные 3 секунды.
    introspect_connect_timeout_seconds: float = Field(
        default=2.0, alias="INTROSPECT_CONNECT_TIMEOUT_SECONDS"
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

    # Размеры пула pooled introspect-клиента (httpx.Limits). Default 20/10 —
    # достаточно для типичного hot-path (один RPS на пользовательский
    # запрос). Под высокой нагрузкой можно поднять без передеплоя.
    introspect_pool_max_connections: int = Field(
        default=20, alias="INTROSPECT_POOL_MAX_CONNECTIONS", ge=1
    )
    introspect_pool_max_keepalive: int = Field(
        default=10, alias="INTROSPECT_POOL_MAX_KEEPALIVE", ge=0
    )

    # Аналог для token-proxy клиента (POST /token swagger-логин). Лимиты
    # ниже introspect'а — логины редкие.
    token_proxy_pool_max_connections: int = Field(
        default=10, alias="TOKEN_PROXY_POOL_MAX_CONNECTIONS", ge=1
    )
    token_proxy_pool_max_keepalive: int = Field(
        default=5, alias="TOKEN_PROXY_POOL_MAX_KEEPALIVE", ge=0
    )

    # Per-IP rate-limit на `POST /events` ingest (синтаксис slowapi: `<n>/<period>`).
    # Quick-win: скомпрометированный shared SERVICE_API_KEY не сможет насытить
    # БД (~3000 ev/s, ~10 GB/h), как только один client IP вылезет за cap.
    # Per-service (per-API-key) keying — отдельно. Health-пробы
    # исключены в `main.py` (см. `_RATE_LIMIT_EXEMPT_PATHS`).
    ingest_rate_limit: str = Field(default="100/minute", alias="INGEST_RATE_LIMIT")

    # Per-IP rate-limit на `GET /events` (read-канал admin/reader).
    # Закрывает Low DoS-вектор: даже без write-доступа атакующий с валидным
    # reader-JWT может вычерпать pgsql-пул широкими COUNT/SELECT'ами по
    # многомиллионному журналу. `audit_count_statement_timeout_ms` ограничивает
    # каждую отдельную query, лимит — частоту таких query от одного клиента.
    # Default консервативный — admin-дашборды редко смотрят чаще 1 req/s.
    audit_query_rate_limit: str = Field(
        default="60/minute", alias="AUDIT_QUERY_RATE_LIMIT"
    )

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

    # Включает `Strict-Transport-Security` на всех ответах. Только за
    # https-фронтом — иначе HTTP-клиенты получают header и ломаются на
    # rebound'е. Симметрично auth_service.
    security_hsts_enabled: bool = Field(
        default=False, alias="SECURITY_HSTS_ENABLED"
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

    # Бюджет на draining self-audit outbox'а при shutdown'е (см.
    # `main.lifespan`). Lifespan ждёт до этого числа секунд, пока drain
    # допишет остаток `asyncio.Queue`; всё, что не успело, теряется и
    # логируется как warning + инкремент `audit_outbox.dropped_shutdown_total`.
    # 2.0s — historic default; обычно достаточно для остатков http.*
    # events, но под нагрузкой / на slow БД полезно поднять.
    audit_drain_timeout_seconds: float = Field(
        default=2.0, alias="AUDIT_DRAIN_TIMEOUT_SECONDS", ge=0.0
    )

    # Размер in-memory буфера self-audit outbox'а. На push-стороне
    # middleware ничего не блокирует: переполнение дропает старейший
    # элемент и инкрементит `dropped_overflow_total`. 4096 рассчитано так, чтобы
    # на 4 uvicorn-воркерах × ~100 RPS spike'а у drain'а было ≥10 секунд
    # форы при пуле в 30 коннектов; поднимать под более тяжёлый трафик.
    audit_outbox_max_size: int = Field(
        default=4096, alias="AUDIT_OUTBOX_MAX_SIZE", ge=1
    )

    # Сколько событий выгребает drain за одну транзакцию. Больше — меньше
    # commit'ов, дольше держим один pooled-коннект; меньше — быстрее
    # отдаём коннект назад в пул. 64 — сбалансированный middle-ground
    # на дефолтном `db_pool_size=10 + max_overflow=20`.
    audit_outbox_batch_size: int = Field(
        default=64, alias="AUDIT_OUTBOX_BATCH_SIZE", ge=1
    )

    # Пауза между батчами drain-loop'а. Положительное число — лёгкий
    # back-pressure, чтобы не дёргать БД transactions по одному событию,
    # если push идёт ровным потоком. На простаивающей очереди никак не
    # сказывается — drain ждёт элемент через `Queue.get()`.
    audit_outbox_poll_interval_seconds: float = Field(
        default=0.05, alias="AUDIT_OUTBOX_POLL_INTERVAL_SECONDS", gt=0.0
    )

    # Включать ли drain-loop в lifespan'е. Дефолт True — production.
    # Тесты могут отключить, если хотят инспектировать очередь руками
    # (fallback в `push_nowait` без started-loop сам выполнит синхронный
    # write через session_factory, что эквивалентно старому поведению).
    audit_outbox_enabled: bool = Field(
        default=True, alias="AUDIT_OUTBOX_ENABLED"
    )

    # Hard cap на длительность COUNT(*) при `include_total=True` в
    # `GET /events`. На multi-million журнале фильтрованный COUNT — второй
    # полный seq-scan, который держит pooled-коннект ~10s и легко
    # превращается в DoS-вектор для admin-дашбордов (один тяжёлый запрос
    # перекрывает остальные читатели). Окружаем COUNT-стейтмент
    # `SET LOCAL statement_timeout`; на превышении репо ловит
    # `QueryCanceled` и возвращает `total=None` — caller трактует None как
    # "точное число неизвестно" и продолжает рендер страницы.
    audit_count_statement_timeout_ms: int = Field(
        default=10000, alias="AUDIT_COUNT_STATEMENT_TIMEOUT_MS", ge=0
    )

    # Тот же гард, но для основного `SELECT ORDER BY timestamp DESC OFFSET
    # LIMIT` в `GET /events`. Под широким фильтром на нескольких миллионах
    # строк он тоже умеет уйти в seq-scan и забить пул коннектов; COUNT —
    # не единственный вектор. Дефолт длиннее, чем у COUNT'а: основной запрос
    # обычно идёт по индексу `timestamp DESC` и завершается за миллисекунды,
    # но при больших OFFSET'ах в дашбордной пагинации может зацепиться. На
    # `57014` репо возвращает пустую страницу + warning лог; caller получает
    # 200 с empty list и не валится в 500.
    audit_query_statement_timeout_ms: int = Field(
        default=30000, alias="AUDIT_QUERY_STATEMENT_TIMEOUT_MS", ge=0
    )

    # Размер чанка для retention-DELETE. Один большой DELETE на миллионы строк
    # держит row-locks на всю выборку, раздувает WAL и тормозит конкурентный
    # ingest. Чанкуем по этому размеру и коммитим каждый чанк — autovacuum
    # успевает чистить dead tuples между коммитами. Дефолт 10_000 — компромисс
    # между WAL-amplification и длительностью одной транзакции; под более
    # тяжёлый журнал оператор может крутить через env, не дёргая deploy.
    retention_chunk_size: int = Field(
        default=10_000, alias="RETENTION_CHUNK_SIZE", ge=1
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

        Пустая строка / `None` → пустой dict. В production пустой map'ы
        отвергается guard'ом `_validate_production_secrets` (service-to-service
        ingest перестал бы работать). В local/dev пустой map допустим, но
        ingest-эндпоинты будут отвечать 401 на любой запрос.
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
        """Production-guard'ы конфигурации.

        `staging` трактуется как prod-like: те же angles атаки
        (network-reachable секрет, MITM на introspect), — конфиг-ошибки не
        должны тихо проходить только из-за того, что env-флаг не `production`.

        Инварианты:
          * `SERVICE_API_KEYS` непустой — без него service-to-service ingest
            не работает вовсе (легаси shared-key режим удалён).
          * `INTROSPECT_SERVICE_API_KEY` непустой и не пересекается со
            значениями `SERVICE_API_KEYS` — иначе утёкший ingest-ключ
            одного сервиса автоматически даёт right на introspect от имени
            loging_service.
          * `APP_DEBUG=false`, `AUTH_SERVICE_URL` задан и https-only
            (loopback исключён), `INTROSPECT_TLS_VERIFY=true` на не-loopback
            https — стандартный набор для MITM-устойчивого introspect-канала.
        """
        if self.app_env in ("production", "staging"):
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in production")

            if not self.service_api_keys:
                raise ValueError(
                    "SERVICE_API_KEYS must be a non-empty JSON map in production "
                    "(legacy single-key SERVICE_API_KEY mode has been removed)"
                )

            if not self.introspect_service_api_key:
                raise ValueError(
                    "INTROSPECT_SERVICE_API_KEY must be set in production"
                )

            # Коллизия introspect-ключа с любым ingest-ключом убивает
            # key-separation, ради которой per-service mode и существует.
            # Если оператор скопировал, например, ключ auth_service и в
            # INTROSPECT_SERVICE_API_KEY — утёкший ingest-ключ auth_service
            # сразу даёт право дёргать /introspect от имени loging_service.
            # Ловим это на старте, а не в post-mortem.
            if self.introspect_service_api_key in self.service_api_keys.values():
                raise ValueError(
                    "INTROSPECT_SERVICE_API_KEY must be distinct from all "
                    "SERVICE_API_KEYS values; reusing an ingest key for introspect "
                    "breaks the per-service key-separation invariant"
                )

            # AUTH_SERVICE_URL обязателен в проде. Без него admin/reader-
            # эндпоинты не падают на старте, но `_fetch_identity` на каждом
            # запросе бросает 503 AUTH_SERVICE_NOT_CONFIGURED — pod выглядит
            # живым, а все JWT-защищённые ручки молча отдают 503. Fail-fast
            # на старте лучше, чем загадочный runtime-503.
            if not self.auth_service_url:
                raise ValueError(
                    "AUTH_SERVICE_URL must be set in production — required for "
                    "JWT introspection on admin/reader endpoints"
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
