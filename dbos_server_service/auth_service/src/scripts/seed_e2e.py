"""Засеять начального account_admin'а для E2E-тестов.

Запускается один раз после `alembic upgrade head` внутри auth-service-e2e
контейнера. Идемпотентен — если юзер уже есть, скипает.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.security import hash_password
from src.models import User
from src.utils.ids import _new_id

ADMIN_USERNAME = os.environ.get("E2E_ADMIN_USERNAME", "e2e_admin")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "E2eAdmin1234!")


def seed() -> None:
    """Создать E2E admin'а если ещё нет."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        if db.query(User).filter_by(username=ADMIN_USERNAME).first():
            print(f"[seed_e2e] user '{ADMIN_USERNAME}' already exists — skipping")
            return
        db.add(User(
            id=_new_id("usr_"),
            username=ADMIN_USERNAME,
            password_hash=hash_password(ADMIN_PASSWORD),
            platform_role="account_admin",
            status="active",
            is_active=True,
        ))
        db.commit()
        print(f"[seed_e2e] created account_admin '{ADMIN_USERNAME}'")


if __name__ == "__main__":
    seed()
