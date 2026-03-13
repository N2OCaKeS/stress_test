# gunicorn_conf.py

import os
from sqlalchemy_utils import database_exists, create_database
from alembic.config import Config as AlembicConfig
from alembic import command
from alembic.util.exc import CommandError
from app.utils.config import settings
from app.db.session import SessionLocal, engine
from app.api.v1.crud.oauth_client import ensure_bootstrap_oauth_clients
from app.api.v1.crud.user import get_user_count, create_user
from app.api.v1.schemas.user import UserCreate

# =========================
# Gunicorn configuration
# =========================
bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"

loglevel = "debug"


def on_starting(server):
    """
    Runs once, before any workers are forked.
    Creates the DB (if missing), runs Alembic migrations,
    and seeds initial users if the table is empty.
    """
    sync_url = settings.DATABASE_URL

    # 1) Create DB if not exists
    if not database_exists(sync_url):
        create_database(sync_url)

    # 2) Run Alembic migrations
    here = os.path.dirname(__file__)
    alembic_cfg = AlembicConfig(os.path.join(here, "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
    try:
        command.upgrade(alembic_cfg, "head")
    except CommandError as e:
        msg = str(e)
        # Если миграционный граф временно разошёлся в несколько head,
        # применяем все heads вместо падения старта API.
        if "Multiple head revisions" in msg or "Multiple heads are present" in msg:
            command.upgrade(alembic_cfg, "heads")
        else:
            raise

    # 3) Seed default users
    db = SessionLocal()
    try:
        if settings.SEED_DEFAULT_ADMIN and get_user_count(db) == 0:
            payload = {
                "login": settings.BASE_ADMIN_USERNAME,
                "password": settings.BASE_ADMIN_PASSWORD,
            }
            # Keep compatibility with both schema variants:
            # new RBAC uses "role", legacy schema uses "is_admin".
            fields = getattr(UserCreate, "model_fields", None)
            if fields is None:
                fields = getattr(UserCreate, "__fields__", {})
            if "role" in fields:
                payload["role"] = "admin"
            elif "is_admin" in fields:
                payload["is_admin"] = True
            create_user(
                db,
                UserCreate(**payload),
            )
        ensure_bootstrap_oauth_clients(db)
    finally:
        db.close()
    # Avoid sharing master-process DB connections with forked workers.
    engine.dispose()


def post_fork(server, worker):
    # Ensure each worker initializes its own fresh DB pool/connections.
    engine.dispose()
