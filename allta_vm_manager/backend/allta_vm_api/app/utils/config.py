from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    DATABASE_URL_ASYNC: str = getenv("DATABASE_URL_ASYNC", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = getenv("AUTH_URL", "https://auth.example.com/introspect")
    API_TOKEN_ADMIN: str = getenv("API_TOKEN_ADMIN", "admin")
    
    VMS_PATH: str = getenv("VMS_PATH", "/vms")
    SSH_CLIENT_KEY: str = getenv("SSH_CLIENTS_KEYS", "/root/.ssh/id_rsa")

    IMAGE_CONFIG_URL: str = getenv('IMAGE_CONFIG_URL', 'example.com')
    TOKEN_VERIFY_URL: str = getenv("TOKEN_VERIFY_URL", "https://auth.example.com/introspect")


settings = Settings()
