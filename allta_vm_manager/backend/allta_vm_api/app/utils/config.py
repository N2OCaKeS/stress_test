from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    DATABASE_URL_ASYNC: str = getenv("DATABASE_URL_ASYNC", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = getenv("AUTH_URL", "https://auth.example.com/introspect")
    
    BROKER_URL: str = getenv("BROKER_URL", "redis://localhost:6379/0")
    BACKEND_URL: str = getenv("BACKEND_URL", "redis://localhost:6379/1")

settings = Settings()
