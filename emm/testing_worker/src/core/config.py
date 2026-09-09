"""Настройки testing_worker'а. Все через env (pydantic-settings).

Трим относительно `testing_service/src/core/config.py`: воркер сейчас —
broker + пустой task-registry (волна 1, каркас), поэтому ему не нужны
поля вроде `database_url`/`auth_service_url`/`server_service_url` — тех у
воркера просто нет потребителя. Появятся вместе с первыми задачами (волна 5
плана миграции), когда worker начнёт реально ходить в server_service/auth_service.
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


@lru_cache
def get_settings() -> Settings:
    """Кэшированный фабричный метод. Один Settings на процесс."""
    return Settings()
