"""One-time bootstrap: create initial account_admin from env vars."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole
from src.core.security import hash_password
from src.repositories.users import UserRepository

logger = logging.getLogger(__name__)


async def bootstrap_admin(db: AsyncSession) -> None:
    settings = get_settings()
    if not settings.initial_admin_username or not settings.initial_admin_password:
        return

    repo = UserRepository(db)
    if await repo.count() > 0:
        return

    await repo.create(
        username=settings.initial_admin_username,
        password_hash=hash_password(settings.initial_admin_password),
        department_id=None,
        email=settings.initial_admin_email,
        platform_role=PlatformRole.ACCOUNT_ADMIN,
        created_by="bootstrap",
    )
    await db.commit()
    logger.info("Bootstrap: created account_admin '%s'", settings.initial_admin_username)
