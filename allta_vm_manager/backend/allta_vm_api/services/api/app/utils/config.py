from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    DATABASE_URL_ASYNC: str = getenv("DATABASE_URL_ASYNC", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = getenv("AUTH_URL", "https://auth.example.com/introspect")
    SERVER_API_BASE: str = getenv("SERVER_API_BASE", "https://auth.example.com/introspect")
    REDIS_URL: str = getenv("REDIS_URL", "redis://localhost:6379/0")
    QUEUE_KEY: str = getenv("QUEUE_KEY", "tasks:vm")
    VMS_HUB_STATUS: str = "vms hub"
    VM_PASS: str = getenv("VM_PASS", "1")
    KEY_FILE_PATH: str = "/data/secret.key"
    
settings = Settings()
