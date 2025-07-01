from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = getenv("AUTH_URL", "https://auth.example.com/introspect")

settings = Settings()
