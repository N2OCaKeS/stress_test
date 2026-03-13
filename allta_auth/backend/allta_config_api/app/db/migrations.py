from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from app.utils.config import settings


def run_migrations() -> None:
    root_dir = Path(__file__).resolve().parents[2]
    alembic_ini = root_dir / "alembic.ini"
    cfg = AlembicConfig(str(alembic_ini))
    cfg.set_main_option("script_location", str(root_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(cfg, "head")
