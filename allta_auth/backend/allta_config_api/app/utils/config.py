from os import getenv

from pydantic_settings import BaseSettings


def _auth_api_url() -> str:
    # Backward compatibility:
    # 1) AUTH_API_URL (new)
    # 2) AUTH_URL (legacy)
    raw = getenv("AUTH_API_URL") or getenv("AUTH_URL") or "https://auth.example.com/api/auth"
    return raw.rstrip("/")


class Settings(BaseSettings):
    AUTH_API_URL: str = _auth_api_url()
    AUTH_CA_BUNDLE: str = getenv("AUTH_CA_BUNDLE", "/etc/allta/tls/allta-api.crt")
    DATABASE_URL: str = getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
    )
    SQL_ECHO: bool = (getenv("SQL_ECHO", "false").strip().lower() in {"1", "true", "yes", "y", "on"})
    DATA_DIR: str = "/data"
    TOKEN_KEY_FILE_PATH: str = "/data/token_secret.key"
    TOKENS_READ_PERMISSION: str = getenv("TOKENS_READ_PERMISSION", "config.tokens")
    CREDENTIALS_READ_PERMISSION: str = getenv("CREDENTIALS_READ_PERMISSION", "config.tokens")
    CREDENTIALS_WRITE_PERMISSION: str = getenv("CREDENTIALS_WRITE_PERMISSION", "config.tokens")


settings = Settings()
