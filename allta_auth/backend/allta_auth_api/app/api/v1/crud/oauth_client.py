import logging
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.models.oauth_client import OAuthClient
from app.utils.config import settings
from app.utils.security import get_password_hash


logger = logging.getLogger(__name__)


DEFAULT_BOOTSTRAP_OAUTH_CLIENTS = (
    {
        "client_id": "allta-portainer",
        "display_name": "Portainer",
        "description": "OAuth client for Portainer UI",
        "required_permission": "portainer",
        "redirect_uri_prefixes": (
            "https://{host}:9443",
            "http://{host}:9000",
            "https://{host}",
            "http://{host}",
            "*",
        ),
    },
    {
        "client_id": "allta-redis",
        "display_name": "Redis",
        "description": "OAuth client for Redis Commander UI",
        "required_permission": "redis.commander",
        "redirect_uri_prefixes": (
            "https://{host}/redis",
            "http://{host}/redis",
        ),
    },
    {
        "client_id": "allta-docs",
        "display_name": "Docs",
        "description": "OAuth client for API documentation UI",
        "required_permission": "docs.api",
        "redirect_uri_prefixes": (
            "https://{host}/api/docs",
            "http://{host}/api/docs",
        ),
    },
    {
        "client_id": "allta-flower",
        "display_name": "Flower",
        "description": "OAuth client for Flower UI",
        "required_permission": "flower",
        "redirect_uri_prefixes": (
            "https://{host}/flower",
            "http://{host}/flower",
        ),
    },
    {
        "client_id": "allta-docker-ui",
        "display_name": "Docker UI",
        "description": "OAuth client for Docker UI",
        "required_permission": "docker",
        "redirect_uri_prefixes": (
            "https://{host}:21502",
            "http://{host}:21502",
        ),
    },
    {
        "client_id": "allta-devpi",
        "display_name": "DevPI",
        "description": "OAuth client for DevPI",
        "required_permission": "devpi",
        "redirect_uri_prefixes": (
            "https://{host}:3141",
            "http://{host}:3141",
        ),
    },
)


def _serialize_csv(values: list[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        if item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return ",".join(normalized)


def list_oauth_clients(db: Session) -> list[OAuthClient]:
    return db.query(OAuthClient).order_by(OAuthClient.client_id.asc()).all()


def get_oauth_client(db: Session, oauth_client_id: int) -> Optional[OAuthClient]:
    return db.query(OAuthClient).filter(OAuthClient.id == oauth_client_id).first()


def get_oauth_client_by_client_id(db: Session, client_id: str) -> Optional[OAuthClient]:
    return db.query(OAuthClient).filter(OAuthClient.client_id == client_id).first()


def create_oauth_client(
    db: Session,
    *,
    client_id: str,
    client_secret: str,
    display_name: str,
    description: str | None,
    redirect_uri_prefixes: list[str],
    required_permission: str | None,
    default_scope: str,
    enabled: bool,
) -> OAuthClient:
    oauth_client = OAuthClient(
        client_id=client_id.strip(),
        client_secret_hash=get_password_hash(client_secret),
        display_name=display_name.strip(),
        description=(description or "").strip() or None,
        redirect_uri_prefixes=_serialize_csv(redirect_uri_prefixes),
        required_permission=(required_permission or "").strip() or None,
        default_scope=(default_scope or "profile").strip() or "profile",
        enabled=enabled,
    )
    db.add(oauth_client)
    try:
        db.commit()
        db.refresh(oauth_client)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth client '{client_id}' already exists",
        )
    return oauth_client


def update_oauth_client(
    db: Session,
    oauth_client: OAuthClient,
    *,
    client_secret: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
    redirect_uri_prefixes: list[str] | None = None,
    required_permission: str | None = None,
    default_scope: str | None = None,
    enabled: bool | None = None,
) -> OAuthClient:
    if client_secret is not None:
        oauth_client.client_secret_hash = get_password_hash(client_secret)
    if display_name is not None:
        oauth_client.display_name = display_name.strip()
    if description is not None:
        oauth_client.description = description.strip() or None
    if redirect_uri_prefixes is not None:
        oauth_client.redirect_uri_prefixes = _serialize_csv(redirect_uri_prefixes)
    if required_permission is not None:
        oauth_client.required_permission = required_permission.strip() or None
    if default_scope is not None:
        oauth_client.default_scope = default_scope.strip() or "profile"
    if enabled is not None:
        oauth_client.enabled = enabled

    db.add(oauth_client)
    db.commit()
    db.refresh(oauth_client)
    return oauth_client


def delete_oauth_client(db: Session, oauth_client: OAuthClient) -> None:
    db.delete(oauth_client)
    db.commit()


def _read_bootstrap_client_secret(client_id: str) -> str | None:
    secret_path = Path(settings.OAUTH_CLIENT_SECRETS_DIR) / f"{client_id}.secret"
    try:
        raw_value = secret_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("OAuth bootstrap secret file is missing: %s", secret_path)
        return None
    secret = raw_value.strip()
    if not secret:
        logger.warning("OAuth bootstrap secret file is empty: %s", secret_path)
        return None
    return secret


def ensure_bootstrap_oauth_clients(db: Session) -> None:
    """
    Создаёт набор OAuth-клиентов из секрета, который генерирует init-контейнер.
    Клиенты создаются только если их ещё нет в БД.
    """
    if not settings.OAUTH_BOOTSTRAP_CLIENTS_ENABLED:
        return

    host = settings.ALLTA_EXTERNAL_HOST.strip() or "allta.devos.astralinux.ru"

    for spec in DEFAULT_BOOTSTRAP_OAUTH_CLIENTS:
        client_id = spec["client_id"].strip()
        if not client_id:
            continue
        existing = get_oauth_client_by_client_id(db, client_id)
        if existing:
            continue

        secret = _read_bootstrap_client_secret(client_id)
        if not secret:
            continue

        redirect_prefixes = [prefix.format(host=host) for prefix in spec["redirect_uri_prefixes"]]
        create_oauth_client(
            db,
            client_id=client_id,
            client_secret=secret,
            display_name=str(spec["display_name"]),
            description=str(spec["description"]),
            redirect_uri_prefixes=redirect_prefixes,
            required_permission=str(spec["required_permission"]),
            default_scope=settings.OAUTH_DEFAULT_SCOPE,
            enabled=True,
        )
