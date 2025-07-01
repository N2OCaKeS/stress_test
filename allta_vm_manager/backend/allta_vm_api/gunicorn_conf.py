import os

from sqlalchemy_utils import database_exists, create_database
from alembic.config import Config as AlembicConfig
from alembic import command

from app.utils.config import settings

# ========== gunicorn ==========
bind = "0.0.0.0:8000"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
loglevel = "debug"

def on_starting(server):
    """
    Запускается ОДИН раз в мастере перед форком воркеров.
    1) создаём БД если нужно
    2) прогоняем alembic upgrade head
    """
    db_url = settings.DATABASE_URL

    # 1) создать БД, если ещё нет
    if not database_exists(db_url):
        create_database(db_url)

    # 2) миграции Alembic
    here = os.path.dirname(__file__)
    alembic_cfg = AlembicConfig(os.path.join(here, "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")
