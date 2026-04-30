"""Application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for auth_service."""

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
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    access_token_ttl_minutes: int = Field(default=10, alias="ACCESS_TOKEN_TTL_MINUTES")
    refresh_token_ttl_days: int = Field(default=14, alias="REFRESH_TOKEN_TTL_DAYS")

    # Docker registry token auth
    docker_registry_service: str = Field(default="registry.example.com", alias="DOCKER_REGISTRY_SERVICE")
    docker_registry_issuer: str = Field(default="auth_service", alias="DOCKER_REGISTRY_ISSUER")
    docker_token_ttl_minutes: int = Field(default=5, alias="DOCKER_TOKEN_TTL_MINUTES")
    # RSA private key in PEM format for signing Docker tokens (RS256).
    # If not set, falls back to HS256 (dev only — Docker registry can't verify HS256).
    # Generate: openssl genrsa -out docker_signing_key.pem 2048
    docker_rsa_private_key: str | None = Field(default=None, alias="DOCKER_RSA_PRIVATE_KEY")

    # OAuth2
    oauth_code_ttl_seconds: int = Field(default=300, alias="OAUTH_CODE_TTL_SECONDS")

    # logging_service integration (optional — falls back to local logger if not set)
    logging_service_url: str | None = Field(default=None, alias="LOGGING_SERVICE_URL")
    logging_service_api_key: str | None = Field(default=None, alias="LOGGING_SERVICE_API_KEY")

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if self.secret_key == "change-me":
                raise ValueError("SECRET_KEY must be changed in production")
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in production")
        return self

    # Bootstrap account_admin (applied only when the users table is empty)
    initial_admin_username: str | None = Field(default=None, alias="INITIAL_ADMIN_USERNAME")
    initial_admin_password: str | None = Field(default=None, alias="INITIAL_ADMIN_PASSWORD")
    initial_admin_email: str | None = Field(default=None, alias="INITIAL_ADMIN_EMAIL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
