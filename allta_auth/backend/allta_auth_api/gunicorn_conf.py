# gunicorn_conf.py

import os
from sqlalchemy_utils import database_exists, create_database
from alembic.config import Config as AlembicConfig
from alembic import command
from app.utils.config import settings
from app.db.session import SessionLocal
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
    command.upgrade(alembic_cfg, "head")

    # 3) Seed default users
    db = SessionLocal()
    try:
        if get_user_count(db) == 0:
            create_user(db, UserCreate(login=os.getenv("BASE_ADMIN_USERNAME", 'admin'),  password=os.getenv("BASE_ADMIN_PASSWORD", 'admin'),  is_admin=True))
    finally:
        db.close()
