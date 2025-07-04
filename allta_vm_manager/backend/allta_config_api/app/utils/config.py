from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    AUTH_API_URL: str = getenv("AUTH_URL", "https://auth.example.com/introspect")

settings = Settings()
