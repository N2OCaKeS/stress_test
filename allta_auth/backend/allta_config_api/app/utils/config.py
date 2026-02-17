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
    DATA_DIR: str = "/data"
    TOKENS_PATH: str = getenv("TOKENS_PATH", "tokens.json")
    TOKENS_READ_PERMISSION: str = getenv("TOKENS_READ_PERMISSION", "config.tokens")


settings = Settings()
