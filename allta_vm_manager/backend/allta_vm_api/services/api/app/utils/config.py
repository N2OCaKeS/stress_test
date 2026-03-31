from os import getenv

from pydantic_settings import BaseSettings


def _auth_api_url() -> str:
    # Backward compatibility:
    # 1) AUTH_API_URL (new)
    # 2) AUTH_URL (legacy)
    raw = getenv("AUTH_API_URL") or getenv("AUTH_URL") or "https://auth.example.com/api/auth"
    return raw.rstrip("/")


def _server_api_base() -> str:
    raw = getenv("SERVER_API_BASE", "https://server.example.com/api/server")
    return raw.rstrip("/")


class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    DATABASE_URL_ASYNC: str = getenv("DATABASE_URL_ASYNC", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = _auth_api_url()
    AUTH_CA_BUNDLE: str = getenv("AUTH_CA_BUNDLE", "/etc/allta/tls/allta-api.crt")
    SERVER_API_BASE: str = _server_api_base()
    VM_MANAGE_PERMISSION: str = getenv("VM_MANAGE_PERMISSION", "vm.manage")
    SERVER_MANAGE_PERMISSION: str = getenv("SERVER_MANAGE_PERMISSION", "server.manage")
    REDIS_URL: str = getenv("REDIS_URL", "redis://localhost:6379/0")
    QUEUE_KEY: str = getenv("QUEUE_KEY", "tasks:vm")
    VMS_HUB_STATUS: str = "vms hub"
    KEY_FILE_PATH: str = "/data/secret.key"


settings = Settings()
