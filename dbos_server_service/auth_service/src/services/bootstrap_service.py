"""One-time bootstrap: создаёт стартового account_admin из ENV.

Запускается на старте. Если БД пуста — создаёт admin'а из
`INITIAL_ADMIN_USERNAME/PASSWORD/EMAIL`. Если хоть один юзер уже есть —
no-op (идемпотентно).
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole
from src.core.security import hash_password
from src.repositories.users import UserRepository
from src.services import audit_service

logger = logging.getLogger(__name__)


async def bootstrap_admin(db: AsyncSession) -> None:
    """Засеять начального account_admin'а если БД пуста.

    Идемпотентно: при непустой БД no-op. На фактическое создание пишем WARNING
    (внеплановый seed = БД пересоздана / kustomize переустановил secret) и
    эмитим CRITICAL `user.create` в audit-канал, чтобы SIEM увидел silent
    re-creation root-юзера и оператор успел сменить дефолтный пароль.
    """
    settings = get_settings()
    if not settings.initial_admin_username or not settings.initial_admin_password:
        return

    repo = UserRepository(db)
    if await repo.count() > 0:
        return

    # `must_change_password=True`: initial admin поднимается с паролем из
    # `INITIAL_ADMIN_PASSWORD` (для k8s deploy — сгенерирован gen_secrets.sh).
    # Пароль виден оператору в k8s-секрете и сертификатах развёртывания, пока
    # admin не сменит его сам. До первой самостоятельной смены через
    # POST /users/me/password middleware режет доступ ко всем endpoint'ам.
    try:
        user = await repo.create(
            username=settings.initial_admin_username,
            password_hash=hash_password(settings.initial_admin_password),
            department_id=None,
            email=settings.initial_admin_email,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            created_by="bootstrap",
            must_change_password=True,
        )
        await db.commit()
    except IntegrityError:
        # Конкурентный запуск нескольких реплик k8s: одна выиграла INSERT,
        # остальные ловят UniqueViolation. count() > 0 проверка не атомарна.
        await db.rollback()
        return
    logger.warning(
        "Bootstrap: created account_admin '%s' from INITIAL_ADMIN_* — "
        "if you didn't expect this, the database has been recreated; "
        "rotate the admin password immediately",
        settings.initial_admin_username,
    )
    audit_service.emit(
        "user.create",
        actor_id="bootstrap",
        actor_type="service",
        target_id=user.id,
        target_type="user",
        status="success",
        allowed=True,
        username=settings.initial_admin_username,
        details={
            "reason": "bootstrap_seed",
            "username": settings.initial_admin_username,
            "platform_role": PlatformRole.ACCOUNT_ADMIN,
        },
    )
