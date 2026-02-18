# app/core/config.py
import warnings
from os import getenv

from pydantic_settings import BaseSettings


_TRUTHY = {"1", "true", "yes", "y", "on"}


def _as_bool(env_name: str, default: bool = False) -> bool:
    value = getenv(env_name)
    if value is None:
        return default
    return value.strip().lower() in _TRUTHY


def _as_int(env_name: str, default: int) -> int:
    value = getenv(env_name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _access_token_expire_minutes() -> int:
    # Backward compatible path:
    # 1) ACCESS_TOKEN_EXPIRE_MINUTES (new)
    # 2) ACCESS_TOKEN_EXPIRE_HOURS (legacy)
    minutes = getenv("ACCESS_TOKEN_EXPIRE_MINUTES")
    if minutes not in (None, ""):
        try:
            return int(minutes)
        except ValueError:
            pass
    return _as_int("ACCESS_TOKEN_EXPIRE_HOURS", 1) * 60


class Settings(BaseSettings):
    DATABASE_URL: str = getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )
    SECRET_KEY: str = getenv("SECRET_KEY", "supersecretkey")
    ALGORITHM: str = getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _access_token_expire_minutes()
    COOKIE_SECURE: bool = _as_bool("COOKIE_SECURE", False)
    SQL_ECHO: bool = _as_bool("SQL_ECHO", False)

    # Docker Registry integration
    REGISTRY_ALLOW_ANON_PULL: bool = _as_bool("REGISTRY_ALLOW_ANON_PULL", True)
    REGISTRY_PUSH_REQUIRES_ADMIN: bool = _as_bool("REGISTRY_PUSH_REQUIRES_ADMIN", True)
    REGISTRY_TOKEN_EXPIRE_SECONDS: int = _as_int("REGISTRY_TOKEN_EXPIRE_SECONDS", 300)
    REGISTRY_TOKEN_ISSUER: str = getenv("REGISTRY_TOKEN_ISSUER", "allta-auth")
    REGISTRY_TOKEN_SERVICE: str = getenv("REGISTRY_TOKEN_SERVICE", "allta-registry")
    REGISTRY_TOKEN_ALGORITHM: str = getenv("REGISTRY_TOKEN_ALGORITHM", "HS256")
    REGISTRY_TOKEN_SECRET_KEY: str = getenv("REGISTRY_TOKEN_SECRET_KEY", SECRET_KEY)
    REGISTRY_TOKEN_PRIVATE_KEY_PATH: str | None = (
        getenv("REGISTRY_TOKEN_PRIVATE_KEY_PATH") or None
    )
    REGISTRY_TOKEN_CERT_PATH: str | None = (
        getenv("REGISTRY_TOKEN_CERT_PATH", "/run/secrets/registry/auth-registry.crt")
        or None
    )

    # Generic integrations (reverse proxy checks + OAuth2)
    DEVPI_WRITE_REQUIRES_ADMIN: bool = _as_bool("DEVPI_WRITE_REQUIRES_ADMIN", True)

    OAUTH_ISSUER: str = getenv("OAUTH_ISSUER", "allta-auth")
    OAUTH_DEFAULT_SCOPE: str = getenv("OAUTH_DEFAULT_SCOPE", "profile")
    OAUTH_CODE_EXPIRE_SECONDS: int = _as_int("OAUTH_CODE_EXPIRE_SECONDS", 120)
    OAUTH_NGINX_CONTEXT_EXPIRE_SECONDS: int = _as_int(
        "OAUTH_NGINX_CONTEXT_EXPIRE_SECONDS",
        1800,
    )
    OAUTH_TOKEN_EXPIRE_SECONDS: int = _as_int("OAUTH_TOKEN_EXPIRE_SECONDS", 300)
    OAUTH_BOOTSTRAP_CLIENTS_ENABLED: bool = _as_bool("OAUTH_BOOTSTRAP_CLIENTS_ENABLED", True)
    OAUTH_CLIENT_SECRETS_DIR: str = getenv(
        "OAUTH_CLIENT_SECRETS_DIR",
        "/run/secrets/registry/oauth_clients",
    )
    ALLTA_EXTERNAL_HOST: str = getenv("ALLTA_EXTERNAL_HOST", "allta.devos.astralinux.ru")

    # Bootstrapping
    SEED_DEFAULT_ADMIN: bool = _as_bool("SEED_DEFAULT_ADMIN", True)
    BASE_ADMIN_USERNAME: str = getenv("BASE_ADMIN_USERNAME", "admin")
    BASE_ADMIN_PASSWORD: str = getenv("BASE_ADMIN_PASSWORD", "admin")


settings = Settings()

if settings.SECRET_KEY == "supersecretkey":
    warnings.warn(
        "SECRET_KEY uses insecure default value; set SECRET_KEY in environment.",
        stacklevel=1,
    )

if settings.SEED_DEFAULT_ADMIN and settings.BASE_ADMIN_PASSWORD == "admin":
    warnings.warn(
        "BASE_ADMIN_PASSWORD uses insecure default value; set env credentials.",
        stacklevel=1,
    )
