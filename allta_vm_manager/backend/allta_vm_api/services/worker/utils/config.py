from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    BROKER_URL: str = getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    RESULT_BACKEND: str = getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
    QUEUE_KEY: str = getenv("QUEUE_KEY", "queue")
    CELERY_QUEUE: str = getenv("CELERY_QUEUE", "celery-vm")
    ASYNC_DATABASE_URL: str = getenv("ASYNC_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")   
settings = Settings()
