"""Application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="logging_service", alias="APP_NAME")
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

    # Shared secret sent by other services in Authorization: Bearer <key>
    service_api_key: str = Field(default="change-me-service-key", alias="SERVICE_API_KEY")

    # auth_service URL for validating loging_admin JWTs
    auth_service_url: str | None = Field(default=None, alias="AUTH_SERVICE_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
