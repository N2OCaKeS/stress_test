from os import getenv

from pydantic_settings import BaseSettings


def _auth_api_url() -> str:
    # Backward compatibility:
    # 1) AUTH_API_URL (new)
    # 2) AUTH_URL (legacy)
    raw = getenv("AUTH_API_URL") or getenv("AUTH_URL") or "https://auth.example.com/api/auth"
    return raw.rstrip("/")


class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    AUTH_API_URL: str = _auth_api_url()
    AUTH_CA_BUNDLE: str = getenv("AUTH_CA_BUNDLE", "/etc/allta/tls/allta-api.crt")
    SERVER_MANAGE_PERMISSION: str = getenv("SERVER_MANAGE_PERMISSION", "server.manage")
    VM_MANAGE_PERMISSION: str = getenv("VM_MANAGE_PERMISSION", "vm.manage")
    SNAPSHOT_PASSWORDS_READ_PERMISSION: str = getenv(
        "SNAPSHOT_PASSWORDS_READ_PERMISSION",
        "server.snapshot_passwords.read",
    )
    SNAPSHOT_PASSWORDS_WRITE_PERMISSION: str = getenv(
        "SNAPSHOT_PASSWORDS_WRITE_PERMISSION",
        "server.snapshot_passwords.write",
    )
    KEY_FILE_PATH: str = "/data/secret.key"


settings = Settings()
