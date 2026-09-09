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

    ssh_connect_timeout_seconds: float = Field(
        default=30.0,
        alias="SSH_CONNECT_TIMEOUT_SECONDS",
        description="Таймаут на TCP-коннект + SSH-handshake + аутентификацию до стенда.",
    )
    ssh_command_timeout_seconds: float = Field(
        default=3600.0,
        alias="SSH_COMMAND_TIMEOUT_SECONDS",
        description=(
            "Грубый общий cap на исполнение команды теста по SSH. Временная "
            "защита от зависшего процесса на стенде — щедрый дефолт в час, "
            "не продуманный per-test timeout (тот появится вместе с реальными "
            "тестами и их конструктором, §8 плана миграции)."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный фабричный метод. Один Settings на процесс."""
    return Settings()
