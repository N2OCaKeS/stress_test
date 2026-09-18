"""Настройки testing_worker'а. Все через env (pydantic-settings).

Волна 5 плана миграции превращает воркер из пустого broker-каркаса в
реальный SSH-исполнитель: он поллит `testing_service` за готовыми
заданиями и подключается к стендам напрямую, поэтому здесь появляются
`testing_service_url`/`testing_service_internal_api_key` (канал
`POST /internal/queue/claim` и `/completed`) и таймауты SSH-сессии.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Контейнер настроек. Поля грузятся из env (case-insensitive) и из .env-файла."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(
        default="local",
        description="Метка окружения: local|dev|test|staging|production.",
    )

    redis_url: str = Field(
        default="redis://redis:6379/3",
        alias="REDIS_URL",
        description=(
            "Redis URL для taskiq-брокера testing_worker'а. Отдельный db-index "
            "на общем redis-контейнере: /0 — server_worker broker, /1 — "
            "auth_service rate-limit (prod), /2 — secret_service rate-limit, "
            "/3 — testing_service/testing_worker. В production обязателен с password."
        ),
    )
    taskiq_queue_name: str = Field(
        default="testing_taskiq",
        alias="TASKIQ_QUEUE_NAME",
        description="Имя очереди taskiq (Redis-список), которую слушает testing_worker.",
    )

    worker_log_level: str = Field(default="INFO", alias="WORKER_LOG_LEVEL")

    testing_service_url: str | None = Field(
        default=None,
        alias="TESTING_SERVICE_URL",
        description=(
            "Базовый URL `testing_service` (например http://testing_service:8004). "
            "Используется для POST /internal/queue/claim и /internal/queue/{id}/completed."
        ),
    )
    testing_service_internal_api_key: str | None = Field(
        default=None,
        alias="TESTING_SERVICE_INTERNAL_API_KEY",
        description=(
            "Shared-secret для канала /internal/queue/*. Должен совпадать с "
            "SERVICE_API_KEYS['testing_worker'] на стороне testing_service. "
            "Отправляется как `Authorization: Bearer <ключ>` вместе с "
            "`X-Service-Identity: testing_worker`."
        ),
    )

    queue_poll_interval_seconds: float = Field(
        default=3.0,
        alias="QUEUE_POLL_INTERVAL_SECONDS",
        description=(
            "Пауза между вызовами claim, когда очередь пуста или сам claim "
            "недоступен. Если claim только что вернул задание — следующий "
            "вызов идёт сразу, без сна, чтобы не терять throughput под нагрузкой."
        ),
    )

    queue_concurrency: int = Field(
        default=4,
        ge=1,
        alias="QUEUE_CONCURRENCY",
        description=(
            "Сколько независимых `run_polling_loop`-слотов крутится КОНКУРЕНТНО "
            "внутри ОДНОГО процесса воркера (см. `main.py::_start_queue_polling_loop`) "
            "— не отдельные процессы/поды, а `asyncio.Task` в одном event loop'е. "
            "Каждый слот сам ходит в `claim()`, `claim_next_ready()` на стороне "
            "testing_service гарантирует (`SELECT ... FOR UPDATE SKIP LOCKED`), "
            "что два слота не заберут один и тот же item. SSH-сессия почти всё "
            "время ждёт сеть, не грузит CPU — конкурентность внутри одного процесса "
            "дешевле, чем N процессов. Значение должно быть не меньше числа "
            "стендов, которые реально могут одновременно готовиться/выполнять "
            "тест, иначе часть стендов будет простаивать в очереди на claim, "
            "даже когда сами физически свободны."
        ),
    )

    interrupt_poll_interval_seconds: float = Field(
        default=7.0,
        alias="INTERRUPT_POLL_INTERVAL_SECONDS",
        description=(
            "Как часто спрашивать `/internal/queue/{id}/interrupt-check`, пока "
            "тест выполняется. Компромисс между задержкой останова (оператор "
            "нажал «Пропустить» — ждёт до этого интервала) и лишней нагрузкой "
            "на testing_service: опрос идёт всё время жизни SSH-сессии, а она "
            "может тянуться часами."
        ),
    )

    preflight_enabled: bool = Field(
        default=True,
        alias="PREFLIGHT_ENABLED",
        description=(
            "Проверять доступность внешних сервисов (Jira/Confluence/git/"
            "releases/DNS) перед запуском теста на стенде. Выключение имеет "
            "смысл в изолированных окружениях, где этих адресов нет вообще."
        ),
    )
    preflight_http_urls: str = Field(
        default=(
            "https://jira.astralinux.ru,"
            "https://life.astralinux.ru,"
            "https://git.astralinux.ru,"
            "https://releases.devos.astralinux.ru"
        ),
        alias="PREFLIGHT_HTTP_URLS",
        description=(
            "Список URL через запятую, каждый из которых должен ответить перед "
            "запуском теста. Дефолт — те же четыре адреса, что проверяло легаси "
            "(allta_image_conf.py: JIRA_URL/CONFLUENCE_URL/GIT_URL/RELEASES_URL). "
            "Пустая строка отключает HTTP-часть проверки."
        ),
    )
    preflight_dns_hosts: str = Field(
        default="10.177.128.198,10.177.180.246,10.177.181.142",
        alias="PREFLIGHT_DNS_HOSTS",
        description=(
            "Корпоративные DNS-серверы через запятую. Достаточно, чтобы "
            "отозвался ЛЮБОЙ из них (легаси-семантика: `0 in available_dns."
            "values()`). Пустая строка отключает DNS-часть проверки."
        ),
    )
    preflight_dns_port: int = Field(
        default=53,
        alias="PREFLIGHT_DNS_PORT",
        description=(
            "Порт для TCP-проверки DNS-серверов. Легаси слало ICMP-ping, но "
            "контейнеру raw-сокеты недоступны — проверяем тот порт, ради "
            "которого сервер и нужен."
        ),
    )
    preflight_probe_timeout_seconds: float = Field(
        default=15.0,
        alias="PREFLIGHT_PROBE_TIMEOUT_SECONDS",
        description="Таймаут одной пробы (один HTTP-запрос либо один TCP-коннект к DNS).",
    )
    preflight_poll_interval_seconds: float = Field(
        default=180.0,
        alias="PREFLIGHT_POLL_INTERVAL_SECONDS",
        description=(
            "Пауза между повторами проверки, пока хоть один сервис недоступен. "
            "Дефолт — легаси `requests_frequency = 180`."
        ),
    )
    preflight_timeout_seconds: float = Field(
        default=7200.0,
        alias="PREFLIGHT_TIMEOUT_SECONDS",
        description=(
            "Сколько всего ждать восстановления внешних сервисов, прежде чем "
            "признать item провалившимся. Дефолт — легаси `wait_time = 120` "
            "минут. Ожидание ограничено специально: зависшая проверка не "
            "должна держать очередь стенда бесконечно."
        ),
    )

    ssh_connect_timeout_seconds: float = Field(
        default=30.0,
        alias="SSH_CONNECT_TIMEOUT_SECONDS",
        description="Таймаут на TCP-коннект + SSH-handshake + аутентификацию до стенда.",
    )
    ssh_command_timeout_seconds: float = Field(
        default=43200.0,
        alias="SSH_COMMAND_TIMEOUT_SECONDS",
        description=(
            "Общий cap на исполнение команды теста по SSH, когда у самого "
            "теста не задан свой `timeout_seconds` (см. `test_definitions`). "
            "Дефолт — 12 часов: часть тестов идёт заметно дольше часа, "
            "щедрого запаса под конкретный тест лучше задавать per-test."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный фабричный метод. Один Settings на процесс."""
    return Settings()
