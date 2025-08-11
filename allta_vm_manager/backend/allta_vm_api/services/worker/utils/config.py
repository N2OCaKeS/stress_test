from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    BROKER_URL = getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    RESULT_BACKEND = getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
    CELERY_PREFETCH = getenv("CELERY_PREFETCH", "1")
settings = Settings()
