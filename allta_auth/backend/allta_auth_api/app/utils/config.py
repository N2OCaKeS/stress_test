# app/core/config.py
from os import getenv

from pydantic_settings import BaseSettings



class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    SECRET_KEY: str = getenv("SECRET_KEY", 'supersecretkey')
    ALGORITHM: str = getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(getenv("ACCESS_TOKEN_EXPIRE_HOURS", 1)) * 60
    COOKIE_SECURE: bool = False

settings = Settings()


