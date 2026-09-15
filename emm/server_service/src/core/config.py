"""Настройки приложения. Все через env (pydantic-settings)."""

import re
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


# Матчит DSN `redis://[user]:<password>@host:port/db`. Без password
# (`redis://redis:6379/0`) и без user:pass-сегмента вообще — не матчит.
# Симметричен с `server_worker/src/core/config.py:_REDIS_URL_PASSWORD_RE`.
_REDIS_URL_PASSWORD_RE = re.compile(r"://[^/@]*:[^@/]+@")

# Hosts, для которых разрешён plain http в prod/staging (devcontainer /
# on-host port-forward, где TLS терминируется на той же машине и MITM-модель
# выглядит иначе). Симметрично `loging_service/src/core/config.py:_LOCAL_HOSTS`.
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
    service_api_keys: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "SERVER_INBOUND_SERVICE_API_KEYS",
            "SERVICE_API_KEYS",
            "service_api_keys",
        ),
        description=(
            "Inbound s2s bearer-ключи: `{identity: shared_secret}`. Используются "
            "ops-эндпоинтами (`/internal/migration_status` для rotate-runner'а) и "
            "бронью серверов от имени сервиса "
            "(`/internal/servers/{id}/acquire-for-service` и соседи — identity "
            "`testing_service` / `acs`), "
            "которые не ходят через auth_service introspect — вместо JWT/PAT/bot "
            "там простой shared-secret под `X-Service-Identity: <identity>`. Каждый "
            "caller получает свой ключ; sender выбирается по identity, ключ "
            "проверяется constant-time'ом. "
            "Формат env: либо JSON-объект "
            "`SERVER_INBOUND_SERVICE_API_KEYS='{\"rotation_runner\":\"<secret>\"}'`, "
            "либо comma-separated kv-list `rotation_runner=<secret>,other=<secret>`. "
            "Пустой dict (default) отключает все ops-эндпоинты — любой вызов "
            "получит 401 SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."
        ),
    )

    @field_validator("service_api_keys", mode="before")
    @classmethod
    def _parse_service_api_keys(cls, v):
        """Поддерживает kv-list (`a=1,b=2`) и JSON-объект в дополнение к нативному dict.

        Pydantic-settings по умолчанию умеет только JSON через env: для удобства
        оператора (одна строка в Secret без экранирования кавычек) принимаем
        и привычный kv-list формат. Пустая строка / None → пустой dict.
        """
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k).strip(): str(val) for k, val in v.items() if str(k).strip()}
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return {}
            if v.startswith("{"):
                import json
                try:
                    parsed = json.loads(v)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"SERVER_INBOUND_SERVICE_API_KEYS: invalid JSON: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ValueError(
                        "SERVER_INBOUND_SERVICE_API_KEYS: JSON must decode to an object"
                    )
                return {str(k).strip(): str(val) for k, val in parsed.items() if str(k).strip()}
            # kv-list: `a:1,b:2` (двоеточие как separator — формат gen_secrets.sh
            # для loging-style maps). Также допускается `a=1,b=2` для совместимости
            # с pyhton-стилем env. Без любого separator или с пустыми частями —
            # отбиваем явно, чтобы оператор не получил тихий пустой dict.
            out: dict[str, str] = {}
            for raw in v.split(","):
                item = raw.strip()
                if not item:
                    continue
                if ":" in item:
                    key, _, value = item.partition(":")
                elif "=" in item:
                    key, _, value = item.partition("=")
                else:
                    raise ValueError(
                        f"SERVER_INBOUND_SERVICE_API_KEYS: item {item!r} missing ':' or '=' separator"
                    )
                key = key.strip()
                value = value.strip()
                if not key or not value:
                    raise ValueError(
                        f"SERVER_INBOUND_SERVICE_API_KEYS: empty key/value in {item!r}"
                    )
                out[key] = value
            return out
        return v
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
            "Сгенерить: `python -c \"import secrets; print(secrets.token_hex(32))\"`. "
            "Этот же salt используется и для `redis_stash_crypto` (info-метка "
            "разная, KDF-output изолирован по domain-separator)."
        ),
    )
    redis_stash_encryption_key: str = Field(
        default="",
        description=(
            "Master-ключ для envelope-шифрования Redis-stash'а кред между "
            "server_service и server_worker (provision/prepare dispatch). "
            "Отдельный от `SERVER_ENCRYPTION_KEY` (тот живёт ТОЛЬКО в "
            "server_service, БД-ciphertext'ы) — этот mount'ится симметрично "
            "в оба сервиса. min_length=32 — нижняя граница энтропии. Сгенерить: "
            "`openssl rand -base64 32`. В production/staging обязателен — без "
            "него server_worker_redis_url триггерит strict-validator."
        ),
    )
    redis_stash_encryption_key_version: int = Field(
        default=1,
        ge=1,
        description=(
            "Активная версия `REDIS_STASH_ENCRYPTION_KEY`, пишется в новые "
            "stash-токены (`v<N>$...`). Ротация: bump'нуть version + положить "
            "новый ключ в `REDIS_STASH_ENCRYPTION_KEY`, старый — в "
            "`REDIS_STASH_ENCRYPTION_KEY__v<N-1>` env (для чтения in-flight "
            "stash'ей). TTL stash'а ~15 минут — окно coexistence версий "
            "короткое, легко закрыть rolling restart'ом обоих сервисов."
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
    dispatch_creds_ttl_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Время жизни inline-кред provision-таски (`account.provision`) "
            "в Redis. Plaintext password + ssh_private_key кладутся под "
            "`dbos:dispatch_creds:<dcd_id>` с этим "
            "TTL, в task-payload едет только `creds_stash_key`. Без stash'а "
            "plaintext висел бы в `dev_server_worker.tasks.payload` JSONB до "
            "retention cleanup'а. 15 минут — запас на несколько retry-попыток "
            "воркера с back-off; после истечения worker fail'ит с "
            "`DISPATCH_STASH_MISSING`, оператор инициирует новый dispatch."
        ),
    )
    os_releases_index_url: str = Field(
        default="https://releases.devos.astralinux.ru/index.json",
        alias="OS_RELEASES_INDEX_URL",
        description=(
            "URL индекса релизов ОС (index.json), из которого строятся repo-URL "
            "для os_version по build-версии. Базис раздачи релизов берётся как "
            "dirname этого URL (mount_point дописывается к нему)."
        ),
    )
    os_releases_index_cache_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        alias="OS_RELEASES_INDEX_CACHE_TTL_SECONDS",
        description=(
            "TTL in-process кэша индекса релизов (секунды). Индекс большой и "
            "общий для всех версий, поэтому держим его в памяти. 0 отключает "
            "кэш (каждый резолв тянет индекс заново)."
        ),
    )
    os_releases_index_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="OS_RELEASES_INDEX_TIMEOUT_SECONDS",
        description=(
            "HTTP-таймаут на загрузку индекса релизов. Индекс крупный, поэтому "
            "запас больше обычных s2s-таймаутов."
        ),
    )
    vm_box_config_url: str = Field(
        default="ftp://10.177.103.10/boxes/test-box-config.json",
        alias="VM_BOX_CONFIG_URL",
        description=(
            "URL конфига боксов-образов ВМ (`test-box-config.json`) на анонимном "
            "FTP. Секция `libvirt_box` (имя→url .tar.gz) синкается в каталог "
            "`vm_images` эндпоинтом POST /vm-images/refresh. Поддерживаются "
            "схемы ftp:// и http(s)://."
        ),
    )
    vm_box_config_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="VM_BOX_CONFIG_TIMEOUT_SECONDS",
        description="Таймаут загрузки конфига боксов-образов ВМ с FTP/HTTP.",
    )
    vm_image_default_user: str = Field(
        default="u",
        alias="VM_IMAGE_DEFAULT_USER",
        description=(
            "Дефолтная гостевая учётка бокса-образа ВМ (у всех боксов `u`, "
            "sudo NOPASSWD, SSH :22). vm.prepare заходит под ней, чтобы поставить "
            "per-VM управляющие креды и снести базовую учётку."
        ),
    )
    vm_image_default_password: str = Field(
        default="1",
        alias="VM_IMAGE_DEFAULT_PASSWORD",
        description=(
            "Дефолтный пароль гостевой учётки образа ВМ (`1`). Едет воркеру в "
            "payload vm.prepare для первого входа sshpass. Единый шифр-конфиг "
            "образа не нужен — значение фиксировано в сборке боксов."
        ),
    )
    vm_console_token_ttl_seconds: int = Field(
        default=120,
        gt=0,
        alias="VM_CONSOLE_TOKEN_TTL_SECONDS",
        description=(
            "Время жизни токена доступа к консоли ВМ (`POST /vms/{id}/console`). "
            "UI получает токен + hub-хост + VNC-порт/serial-путь и подключается "
            "через websockify/PTY-прокси, который валидирует токен в этом окне."
        ),
    )
    vm_console_token_secret: str = Field(
        default="dev-vm-console-token-secret",
        alias="VM_CONSOLE_TOKEN_SECRET",
        description=(
            "Общий секрет для подписи токенов графической консоли ВМ (vnc/spice). "
            "server_service подписывает токен HMAC-SHA256, console-прокси проверяет "
            "им подпись и срок — round-trip в server_service не нужен. В проде "
            "обязателен свой секрет; дефолт — только для dev/тестов."
        ),
    )
    vm_console_proxy_ws_base: str = Field(
        default="wss://vm-console-proxy",
        alias="VM_CONSOLE_PROXY_WS_BASE",
        description=(
            "Базовый ws(s)-адрес console-прокси для графических консолей ВМ. "
            "ws_url в ответе `POST /vms/{id}/console` = <base> + ws_path; UI "
            "открывает его, предъявляя подписанный токен."
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
    secret_service_url: str = Field(default="", alias="SECRET_SERVICE_URL")
    secret_service_api_key: str = Field(default="", alias="SECRET_SERVICE_API_KEY")
    secret_request_timeout_seconds: float = Field(default=10.0, gt=0, alias="SECRET_REQUEST_TIMEOUT_SECONDS")

    # ── Исходящий канал в testing_service (callback prepare-for-test) ──────
    testing_service_url: str = Field(
        default="",
        alias="TESTING_SERVICE_URL",
        description=(
            "Base URL testing_service, куда уходит callback завершения "
            "`prepare-for-test` (POST <base>/internal/prepare-for-test/"
            "{prepare_request_id}/completed). Пусто — callback не шлётся, "
            "результат пайплайна остаётся только в "
            "`server_prepare_for_test_requests` (потребителя ещё нет)."
        ),
    )
    testing_service_api_key: str = Field(
        default="",
        alias="TESTING_SERVICE_API_KEY",
        description=(
            "Shared service-to-service secret для callback'а в "
            "testing_service. Уходит как 'Authorization: Bearer <key>' "
            "вместе с 'X-Service-Identity: server_service'. Пусто — callback "
            "не шлётся."
        ),
    )
    testing_callback_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        alias="TESTING_CALLBACK_TIMEOUT_SECONDS",
        description=(
            "Таймаут одного POST'а callback'а в testing_service. Callback "
            "уходит уже после того, как пайплайн завершился, и не блокирует "
            "worker-callback: на исчерпании ретраев результат остаётся в БД."
        ),
    )

    # ── Pool'ы под исходящие каналы в loging_service ───────────────────────
    # Audit-emit (write) и dashboard-read держим в раздельных пулах: drift-burst
    # на read-эндпоинт не должен выедать FD у audit-канала и наоборот. Размеры
    # — sane defaults под одного uvicorn worker'а; на сильно нагруженных
    # стендах поднимать env'ом без правки кода.
    audit_pool_max_connections: int = Field(
        default=20,
        ge=1,
        alias="AUDIT_POOL_MAX_CONNECTIONS",
        description=(
            "Верхний лимит TCP-соединений в пуле audit-emit к loging_service. "
            "Каждый authenticated request может породить emit (grant/ban/power/"
            "http.client_error); per-call client под slowloris-burst быстро "
            "исчерпает FD-пул, pool ставит back-pressure."
        ),
    )
    audit_pool_max_keepalive: int = Field(
        default=10,
        ge=0,
        alias="AUDIT_POOL_MAX_KEEPALIVE",
        description=(
            "Сколько idle keep-alive соединений audit-pool держит открытыми. "
            "Должно быть <= audit_pool_max_connections."
        ),
    )
    audit_pool_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        alias="AUDIT_POOL_TIMEOUT_SECONDS",
        description=(
            "Таймаут одного POST'а в loging_service /events. Best-effort: "
            "audit-emit не блокирует main-flow, на превышении emit теряется "
            "(WARNING-лог + fallback в локальный logger)."
        ),
    )
    register_events_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        alias="REGISTER_EVENTS_TIMEOUT_SECONDS",
        description=(
            "Таймаут sync `httpx.post` на регистрацию каталога событий в "
            "loging_service (startup). Жирный timeout относительно общего "
            "_STARTUP_AUDIT_TIMEOUT_SECONDS (10s) съедает бюджет на следующий "
            "за ним `service.started` emit; держим узкий 2.0s — на медленном "
            "loging регистрация просто пропускается с WARNING, lifecycle-event "
            "успевает уйти."
        ),
    )
    loging_read_pool_max_connections: int = Field(
        default=10,
        ge=1,
        alias="LOGING_READ_POOL_MAX_CONNECTIONS",
        description=(
            "Лимит соединений read-канала к loging_service "
            "(`GET /events` под drift-summary). Отдельный пул от audit-emit, "
            "чтобы дашборд-burst не выедал FD у write-канала."
        ),
    )
    loging_read_pool_max_keepalive: int = Field(
        default=5,
        ge=0,
        alias="LOGING_READ_POOL_MAX_KEEPALIVE",
        description="Idle keep-alive для loging-read pool'а.",
    )
    loging_read_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        alias="LOGING_READ_TIMEOUT_SECONDS",
        description=(
            "Таймаут чтения из loging_service. Дольше audit-emit'а: drift-агрегация "
            "может тянуть несколько секунд за окно событий."
        ),
    )
    acs_pool_max_connections: int = Field(
        default=10,
        ge=1,
        alias="ACS_POOL_MAX_CONNECTIONS",
        description=(
            "Лимит соединений pooled-клиента к ACS (снимки дисков физических "
            "серверов через Clonezilla). `base_url` у ACS динамический (хранится "
            "в `AcsSettings`, меняется через `/settings/acs`), поэтому клиент в "
            "пуле не привязан к конкретному host'у — полный URL идёт per-call."
        ),
    )
    acs_pool_max_keepalive: int = Field(
        default=5,
        ge=0,
        alias="ACS_POOL_MAX_KEEPALIVE",
        description="Idle keep-alive для ACS-пула.",
    )
    acs_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="ACS_REQUEST_TIMEOUT_SECONDS",
        description=(
            "Таймаут HTTP-вызовов в ACS. Сами save-disk/restore-backup — "
            "асинхронные (ACS отвечает сразу и дальше сам гоняет celery-цепочку "
            "с ребутом), но 15s с запасом на медленный ответ самого HTTP-приёма."
        ),
    )
    global_rate_limit: str = Field(
        default="120/second",
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
    verify_future_skew_seconds: int = Field(
        default=60,
        ge=1,
        alias="VERIFY_FUTURE_SKEW_SECONDS",
        description=(
            "Отдельное окно для future-dated `verified_at`: насколько в "
            "будущее worker может опередить локальные часы server_service'а "
            "до отбоя 400 BMC_VERIFY_REQUIRED (reason=verify_in_future). "
            "По умолчанию равно `IPMI_VERIFY_MAX_AGE_SECONDS` (60s); "
            "повышать (например, до 300s) на стендах с заметным NTP-skew "
            "между worker'ом и приёмником, где штатный 60s окном съедает "
            "обычные расхождения. Stale-направление продолжает идти через "
            "`IPMI_VERIFY_MAX_AGE_SECONDS` — оно про возраст successful "
            "verify, не про clock-skew."
        ),
    )
    rotated_at_skew_seconds: int = Field(
        default=600,
        ge=1,
        alias="ROTATED_AT_SKEW_SECONDS",
        description=(
            "Допустимый перекос (секунды) между `rotated_at` от worker'а и "
            "локальным временем server_service'а в "
            "`record_ipmi_credentials_rotated`. Защита от NTP-drift'а: "
            "значения за пределами окна отбиваются как ROTATED_AT_IN_FUTURE "
            "/ ROTATED_AT_TOO_OLD. 10 минут — баланс между терпимостью к "
            "обычному кластерному NTP-skew и блокировкой умышленно поданных "
            "дат для top-sort'а в UI."
        ),
    )
    ipmi_rotate_per_server_rate_limit: str = Field(
        default="120/second",
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
        default="120/second",
        alias="SERVER_PREPARE_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на bootstrap-управления через POST "
            "/servers/{id}/prepare (синтаксис slowapi). Каждый dispatch "
            "пишет plaintext-creds в Redis под TTL и поднимает worker-task, "
            "поэтому burst либо забивает Redis сиротскими ключами, либо "
            "перегружает worker. Применяется поверх `global_rate_limit`."
        ),
    )
    bulk_prepare_rate_limit: str = Field(
        default="120/second",
        alias="BULK_PREPARE_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на POST /servers/prepare/bulk (массовый "
            "онбординг через worker). Один запрос шедулит N задач (по числу "
            "серверов в теле) и кладёт N plaintext-stash'ей в Redis — burst "
            "опаснее single-prepare. Применяется поверх `global_rate_limit`."
        ),
    )
    bulk_prepare_max_servers: int = Field(
        default=50,
        ge=1,
        alias="BULK_PREPARE_MAX_SERVERS",
        description=(
            "Cap на число серверов в одном POST /servers/prepare/bulk. При "
            "превышении endpoint отбивается 413 — оператор должен раздробить "
            "батч. Каждый сервер несёт plaintext-bootstrap-креды в Redis под "
            "TTL, поэтому cap ниже mass-rotation."
        ),
    )
    ipmi_credentials_rotate_rate_limit: str = Field(
        default="120/second",
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
        default="120/second",
        alias="ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на POST /server-accounts/{id}/rotate_password — "
            "user-initiated ротация общего ciphertext'а без SSH-apply. CRITICAL-"
            "аудит; burst грозит шумом и парой race'ов с параллельным "
            "/rotate-dispatch'ем. Применяется поверх `global_rate_limit`."
        ),
    )
    mass_rotate_dispatch_rate_limit: str = Field(
        default="120/second",
        alias="MASS_ROTATE_DISPATCH_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на POST /server-accounts/{id}/rotate (mass-"
            "rotation dispatch через worker). Один запрос с mode=all шедулит "
            "N задач (по числу привязанных серверов); burst пробивает worker-"
            "redis и outbox. Применяется поверх `global_rate_limit`."
        ),
    )
    mass_rotation_max_servers: int = Field(
        default=200,
        ge=1,
        alias="MASS_ROTATION_MAX_SERVERS",
        description=(
            "Cap на число серверов в одном mass-rotation запросе. При "
            "превышении endpoint отбивается 413 (request entity too large) — "
            "оператор должен раздробить ротацию на батчи или сделать точечный "
            "rotate по server_id'у. Дефолт 200 покрывает крупные "
            "department'ы; снизить при перегруженном worker'е."
        ),
    )
    fanout_update_on_host_max: int = Field(
        default=200,
        ge=1,
        alias="FANOUT_UPDATE_ON_HOST_MAX",
        description=(
            "Cap на размер fan-out'а `account.update_on_host` (PATCH одного "
            "управляемого атрибута аккаунта). PATCH с N привязанных серверов "
            "шедулит N dispatch'ей по одному per host; при превышении cap'а "
            "endpoint отбивает 413, эмитит `fanout_update_on_host.truncated`. "
            "Дефолт симметричен `mass_rotation_max_servers`."
        ),
    )
    management_user_sync_fanout_max: int = Field(
        default=500,
        ge=1,
        alias="MANAGEMENT_USER_SYNC_FANOUT_MAX",
        description=(
            "Cap на размер фан-аута `management_user_sync` при PUT "
            "`/management-user-config`. Конфиг управляющей учётки — "
            "платформенный singleton, поэтому изменение бьёт high-priority "
            "задачей по всем подготовленным (`is_managed`) серверам платформы. "
            "При превышении cap'а режем хвост и эмитим "
            "`management_user_sync_fanout.truncated`; недобитые серверы "
            "выровняются следующим PUT / prepare. Дефолт крупнее обычных "
            "fan-out'ов — учётка одна на всю платформу."
        ),
    )
    auto_inventory_fanout_max: int = Field(
        default=500,
        ge=1,
        alias="AUTO_INVENTORY_FANOUT_MAX",
        description=(
            "Cap на размер планового авто-inventory прогона (worker-scheduler → "
            "internal /servers/auto-inventory-sweep): по всем подготовленным "
            "(`is_managed`) серверам платформы ставится inventory.sync + "
            "power.status. Работает как throttle против шторма задач; при "
            "превышении режем хвост и эмитим `auto_inventory_sweep.truncated`, "
            "недобитые серверы выровняются следующим прогоном. Дефолт совпадает "
            "с `management_user_sync_fanout_max` — оба бьют по всей платформе."
        ),
    )
    astra_update_stuck_ttl_minutes: int = Field(
        default=60,
        ge=1,
        alias="ASTRA_UPDATE_STUCK_TTL_MINUTES",
        description=(
            "Порог зависшей OS-update блокировки. Если сервер держит "
            "`busy_state='updating'` дольше этого времени (по `busy_since`), а "
            "callback `astra-updated` так и не пришёл (воркер упал/потерял "
            "задачу), плановый sweep освобождает его в `free` и эмитит "
            "`server.astra_update_recovered`. Без этого залипший updating "
            "блокировал бы все операции над сервером навсегда. Считать с запасом "
            "относительно самого долгого реального обновления ОС."
        ),
    )
    installed_packages_bulk_max_servers: int = Field(
        default=50,
        ge=1,
        alias="INSTALLED_PACKAGES_BULK_MAX_SERVERS",
        description=(
            "Cap на число серверов в одном bulk-запросе пакетов "
            "(`POST /servers/installed-packages/bulk`). Каждый сервер из "
            "списка порождает отдельную `installed_packages.list` (SSH-probe), "
            "так что батч прямо нагружает worker-пул; при превышении endpoint "
            "отбивает 413. Дефолт 50 ниже mass-rotation'а — сводную таблицу "
            "пакетов обычно собирают по выборке, а не по всему department'у."
        ),
    )
    worker_pool_rate_limit: str = Field(
        default="120/second",
        alias="WORKER_POOL_RATE_LIMIT",
        description=(
            "Per-IP rate-limit на seed/claim outbox-эндпоинты в "
            "/internal/secrets/. Worker по контракту poll'ит batch'ами раз в "
            "секунду; отдельный лимит 120/second задаёт потолок на случай bug'а "
            "в worker'е (два poller'а / loop без back-off'а)."
        ),
    )
    # ── Фоновый дренер reencrypt-outbox (self-drain внутри server_service) ──
    # Раньше дренаж делал server_worker через taskiq-scheduler, которого в
    # проде нет. Теперь каждый инстанс поднимает собственный asyncio-таск,
    # клеймит батчи через FOR UPDATE SKIP LOCKED (шардинг по репликам) и
    # перешифровывает секреты под активную версию ключа.
    reencrypt_drain_enabled: bool = Field(
        default=True,
        alias="REENCRYPT_DRAIN_ENABLED",
        description=(
            "Поднимать ли фоновый дренер reencrypt-outbox в lifespan. "
            "True (default) — сервис сам осушает outbox без worker'а. "
            "False отключает таск (например, если дренаж делает внешний runner)."
        ),
    )
    reencrypt_lazy_batch_size: int = Field(
        default=25,
        ge=1,
        alias="REENCRYPT_LAZY_BATCH_SIZE",
        description=(
            "Размер батча claim'а в lazy-режиме (force не активен). Малый — "
            "чтобы фоновая перешифровка не мешала пользовательской нагрузке."
        ),
    )
    reencrypt_lazy_sleep_seconds: float = Field(
        default=5.0,
        gt=0,
        alias="REENCRYPT_LAZY_SLEEP_SECONDS",
        description=(
            "Пауза между итерациями дренера в lazy-режиме, когда работа была. "
            "Троттлит нагрузку на БД."
        ),
    )
    reencrypt_force_batch_size: int = Field(
        default=200,
        ge=1,
        alias="REENCRYPT_FORCE_BATCH_SIZE",
        description=(
            "Размер батча claim'а в force-режиме. Крупнее lazy — дренить надо "
            "максимально быстро, сервис всё равно закрыт maintenance-gate'ом."
        ),
    )
    reencrypt_force_sleep_seconds: float = Field(
        default=0.05,
        ge=0,
        alias="REENCRYPT_FORCE_SLEEP_SECONDS",
        description=(
            "Пауза между итерациями дренера в force-режиме. Минимальная — "
            "плотный цикл до полного осушения."
        ),
    )
    reencrypt_idle_sleep_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="REENCRYPT_IDLE_SLEEP_SECONDS",
        description=(
            "Пауза дренера, когда pending-очередь пуста (нечего делать). "
            "Опрос outbox'а раз в N секунд без нагрузки на БД."
        ),
    )
    reencrypt_throughput_per_second: float = Field(
        default=25.0,
        gt=0,
        alias="REENCRYPT_THROUGHPUT_PER_SECOND",
        description=(
            "Оценка пропускной способности перешифровки (строк/сек) для "
            "расчёта ETA и заголовка Retry-After в maintenance-gate. "
            "Консервативная константа; занижать безопаснее (клиент подождёт "
            "чуть дольше), завышать — рискнуть ранним повтором."
        ),
    )
    reencrypt_retry_after_buffer_seconds: int = Field(
        default=5,
        ge=0,
        alias="REENCRYPT_RETRY_AFTER_BUFFER_SECONDS",
        description=(
            "Добавка к расчётному ETA в заголовке Retry-After maintenance-"
            "gate'а — запас на то, что дренаж чуть медленнее оценки."
        ),
    )
    reencrypt_retry_after_min_seconds: int = Field(
        default=15,
        ge=1,
        alias="REENCRYPT_RETRY_AFTER_MIN_SECONDS",
        description=(
            "Нижняя граница Retry-After maintenance-gate'а (сек). Клиент не "
            "долбит сервис чаще, даже когда остаток крошечный."
        ),
    )
    reencrypt_retry_after_max_seconds: int = Field(
        default=300,
        ge=1,
        alias="REENCRYPT_RETRY_AFTER_MAX_SECONDS",
        description=(
            "Потолок Retry-After maintenance-gate'а (сек). На огромном остатке "
            "клиенту не отдаётся заведомо гигантский обратный отсчёт."
        ),
    )
    reencrypt_gate_cache_ttl_seconds: float = Field(
        default=2.0,
        ge=0,
        alias="REENCRYPT_GATE_CACHE_TTL_SECONDS",
        description=(
            "TTL in-process кэша состояния force-флага в maintenance-gate'е. "
            "Ограничивает частоту read'ов флага из БД под нагрузкой; при снятии "
            "force gate разблокирует сервис с задержкой не больше этого TTL."
        ),
    )
    password_reveal_rate_limit: str = Field(
        default="120/second",
        alias="PASSWORD_REVEAL_RATE_LIMIT",
        description=(
            "Per-IP+target rate-limit на reveal-эндпоинты, отдающие plaintext "
            "password: GET /server-accounts/{id} (с view_password) и "
            "GET /servers/{id}/ipmi (с view_credentials). Ключ — IP+target_id "
            "(см. `limiter.per_account_key`), чтобы burst против одной цели "
            "не разносился по нескольким account_id'ам с одного IP'шника. "
            "Default 120/second оставляет место под штатные UI-перерисовки "
            "карточки. Применяется поверх `global_rate_limit`."
        ),
    )
    password_reveal_audit_window_seconds: int = Field(
        default=300,
        ge=0,
        alias="PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS",
        description=(
            "Окно throttling'а CRITICAL-аудита раскрытия паролей. На каждом "
            "GET карточки с view_password первое раскрытие per (actor, account) "
            "в окне пишет CRITICAL `server_account.password_revealed`; "
            "последующие в том же окне — INFO `server_account.password_revealed_throttled`, "
            "чтобы UI-tooltip с автообновлением каждые N секунд не забивал SIEM "
            "CRITICAL'ом на один и тот же reveal. 0 отключает throttling (каждый "
            "GET → CRITICAL, старое поведение). Default — 5 минут."
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
    swagger_ui_assets_base: str = Field(
        default="",
        alias="SWAGGER_UI_ASSETS_BASE",
        description=(
            "Базовый URL self-host бандла Swagger UI (например "
            "`http://localhost:8088`). Пусто → /docs тянет ассеты с CDN "
            "jsdelivr. Задан → /docs раздаётся своим маршрутом с этого адреса."
        ),
    )
    # Начальная базовая парольная политика (server-аккаунты / IPMI-креды).
    # Читается на старте в процессный кэш ДО чтения singleton'а из БД, так что
    # это дефолт «из коробки». Как только account_admin поменяет политику в UI
    # (PUT /admin/password-policy), её значение ложится строкой в БД и с этого
    # момента перекрывает env при каждом старте/чтении.
    password_policy_min_length: int = Field(
        default=8, ge=1, le=128,
        alias="PASSWORD_POLICY_MIN_LENGTH",
        description="Начальная мин. длина пароля (перенастраивается в UI).",
    )
    password_policy_require_letter: bool = Field(
        default=True,
        alias="PASSWORD_POLICY_REQUIRE_LETTER",
        description="Начальное требование буквы в пароле (перенастраивается в UI).",
    )
    password_policy_require_digit: bool = Field(
        default=True,
        alias="PASSWORD_POLICY_REQUIRE_DIGIT",
        description="Начальное требование цифры в пароле (перенастраивается в UI).",
    )
    internal_require_dept_header: bool = Field(
        default=True,
        description=(
            "Исторический флаг soft/strict для `X-Target-Department-Id` на "
            "/api/server/v1/internal/*. Сейчас проверка заголовка безусловна "
            "(отсутствует → 403 TARGET_DEPARTMENT_HEADER_REQUIRED, не совпал с "
            "server.department_id → 404), т.к. заголовок — единственный "
            "cross-dept гард глобального worker-бота, и enforce'ится всегда "
            "независимо от этого значения. Поле оставлено для совместимости "
            "конфигов; на авторизацию больше не влияет."
        ),
    )

    # Unix-группы, добавление в которые эскалирует учётку до sudo — поэтому
    # добавление любой из них под обычным `update` запрещено, как и has_sudo→true:
    # требуется action `grant_sudo`. Снятие таких групп / has_sudo→false —
    # допустимо обычным `update` (понижение привилегии).
    #
    # Дефолт: `astra-admin` (GID 1001 на Astra Linux SE даёт sudo), `sudo`
    # (Debian/Astra sudoer-группа), `wheel` (RHEL/Alpine). Через env:
    # comma-separated (`sudo,astra-admin,wheel`) или JSON-list
    # (`SUDO_CONFERRING_GROUPS='["sudo","wheel"]'`).
    sudo_conferring_groups: frozenset[str] = Field(
        default=frozenset({"sudo", "astra-admin", "wheel"}),
        alias="SUDO_CONFERRING_GROUPS",
    )

    @field_validator("sudo_conferring_groups", mode="before")
    @classmethod
    def _parse_sudo_conferring_groups(cls, v):
        """Поддерживает comma-separated string и JSON-list поверх нативного set/list."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return frozenset()
            if v.startswith("["):
                import json
                try:
                    parsed = json.loads(v)
                except json.JSONDecodeError:
                    return frozenset({v})
                if isinstance(parsed, list):
                    return frozenset(str(x).strip() for x in parsed if str(x).strip())
                return frozenset({str(parsed).strip()})
            return frozenset(item.strip() for item in v.split(",") if item.strip())
        if isinstance(v, (list, set, tuple)):
            return frozenset(str(x).strip() for x in v if str(x).strip())
        return v

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
        if self.audit_pool_max_keepalive > self.audit_pool_max_connections:
            raise ValueError(
                f"AUDIT_POOL_MAX_KEEPALIVE ({self.audit_pool_max_keepalive}) "
                f"cannot exceed AUDIT_POOL_MAX_CONNECTIONS ({self.audit_pool_max_connections})"
            )
        if self.loging_read_pool_max_keepalive > self.loging_read_pool_max_connections:
            raise ValueError(
                f"LOGING_READ_POOL_MAX_KEEPALIVE ({self.loging_read_pool_max_keepalive}) "
                f"cannot exceed LOGING_READ_POOL_MAX_CONNECTIONS ({self.loging_read_pool_max_connections})"
            )
        if self.acs_pool_max_keepalive > self.acs_pool_max_connections:
            raise ValueError(
                f"ACS_POOL_MAX_KEEPALIVE ({self.acs_pool_max_keepalive}) "
                f"cannot exceed ACS_POOL_MAX_CONNECTIONS ({self.acs_pool_max_connections})"
            )
        return self

    @model_validator(mode="after")
    def _check_reencrypt_retry_after_bounds(self) -> "Settings":
        """min не может превышать max в окне Retry-After maintenance-gate'а."""
        if self.reencrypt_retry_after_min_seconds > self.reencrypt_retry_after_max_seconds:
            raise ValueError(
                f"REENCRYPT_RETRY_AFTER_MIN_SECONDS "
                f"({self.reencrypt_retry_after_min_seconds}) cannot exceed "
                f"REENCRYPT_RETRY_AFTER_MAX_SECONDS "
                f"({self.reencrypt_retry_after_max_seconds})"
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
        if (
            self.app_env.lower() in {"production", "staging"}
            and self.server_worker_redis_url
            and not _REDIS_URL_PASSWORD_RE.search(self.server_worker_redis_url)
        ):
            raise ValueError(
                "SERVER_WORKER_REDIS_URL must contain a password in "
                f"{self.app_env} (format: redis://:<password>@host:port/db). "
                "Anonymous Redis exposes the taskiq queue to RPUSH from "
                "any co-located container."
            )
        return self

    @model_validator(mode="after")
    def _require_redis_stash_key_in_prod(self) -> "Settings":
        """В production/staging `REDIS_STASH_ENCRYPTION_KEY` обязателен.

        Без ключа `redis_stash_crypto.encrypt_stash` упадёт на первом же
        dispatch'е (`min_length=32` Field-level не покрывает дефолтную пустую
        строку — Field-level check там стоит как `default=""`). Без strict-
        guard'а пустота словится только в рантайме при первом /provision,
        что хуже fail-fast'а на старте. Если `server_worker_redis_url`
        пустой — dispatch отключён, ключ не нужен.
        """
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        if not self.server_worker_redis_url:
            return self
        if not self.redis_stash_encryption_key or len(self.redis_stash_encryption_key) < 32:
            raise ValueError(
                f"REDIS_STASH_ENCRYPTION_KEY must be set (min 32 chars) in "
                f"{self.app_env} when SERVER_WORKER_REDIS_URL is configured. "
                "Generate: openssl rand -base64 32"
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
        if scheme == "http" and not _is_intracluster_host(host):
            raise ValueError(
                "LOGGING_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r}); "
                "audit events carry actor PII and security-sensitive data"
            )
        return self

    @model_validator(mode="after")
    def _require_https_testing_url_in_prod(self) -> "Settings":
        """В production/staging `TESTING_SERVICE_URL` должен быть https://.

        Тот же фильтр, что у auth/logging. Callback несёт пароль и приватный
        SSH-ключ тестового пользователя стенда — на plain http через внешний
        FQDN это выдача рабочего доступа к стенду любому, кто слушает канал.
        Localhost и внутрикластерные имена — исключение, как у соседей.
        Пустой URL (callback отключён) проверку пропускает.
        """
        if self.app_env.lower() not in {"production", "staging"}:
            return self
        if not self.testing_service_url:
            return self
        parsed = urlparse(self.testing_service_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme == "http" and not _is_intracluster_host(host):
            raise ValueError(
                "TESTING_SERVICE_URL must use https:// in "
                f"{self.app_env} (got scheme={scheme!r}, host={host!r}); "
                "the prepare-for-test callback carries the test user's "
                "password and private SSH key"
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
        if scheme == "http" and not _is_intracluster_host(host):
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
