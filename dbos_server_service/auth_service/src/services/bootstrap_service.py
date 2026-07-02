"""Startup-bootstrap: account_admin, реестр платформенных сервисов и worker-бот.

Запускается на старте auth_service. Все шаги идемпотентны и safe к гонке
реплик (проверка существования + catch IntegrityError):

* `bootstrap_admin` — стартовый account_admin из `INITIAL_ADMIN_*`, если БД пуста.
* `bootstrap_platform_services` — регистрация канонического списка сервисов
  (`PLATFORM_SERVICES`), чтобы grant отдела на любой из них не падал 404.
* `bootstrap_worker_bot` — системный отдел, роль worker_bot@server_service и
  бот `server_worker` с токеном из `WORKER_BOT_TOKEN` (только если токен задан).
"""

import logging
import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import (
    PLATFORM_SERVICES,
    SYSTEM_DEPARTMENT_NAME,
    TOKEN_PREFIX_LEN,
    WORKER_BOT_NAME,
    WORKER_BOT_ROLE,
    WORKER_BOT_SERVICE,
    PlatformRole,
)
from src.core.security import hash_opaque_token, hash_password
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
    # Dev-режим: env `DBOS_BOOTSTRAP_NO_FORCE_CHANGE=true` отключает требование
    # смены пароля при первом логине. Включён только в docker-compose.dev.yml;
    # для k8s/prod-манифестов остаётся дефолт True.
    force_change = (
        os.environ.get("DBOS_BOOTSTRAP_NO_FORCE_CHANGE", "").lower() != "true"
    )
    try:
        user = await repo.create(
            username=settings.initial_admin_username,
            password_hash=hash_password(settings.initial_admin_password),
            department_id=None,
            email=settings.initial_admin_email,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            created_by="bootstrap",
            must_change_password=force_change,
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


async def bootstrap_platform_services(db: AsyncSession) -> None:
    """Зарегистрировать канонический список платформенных сервисов.

    Идемпотентно: каждый сервис заводится в своей транзакции, уже существующие
    (заведённые вручную / прошлым стартом) пропускаются, description не
    перетирается. На фактическое создание пишем CRITICAL `service.create` в
    audit-канал (симметрично `platform_service_service.create_service`).
    """
    from src.repositories.services import ServiceRepository

    repo = ServiceRepository(db)
    for service_name, description in PLATFORM_SERVICES:
        if await repo.exists(service_name):
            continue
        try:
            await repo.create(service_name, description)
            await db.commit()
        except IntegrityError:
            # Гонка реплик: другой pod успел зарегистрировать сервис между
            # `exists` и `create`. count-проверка не атомарна — глотаем.
            await db.rollback()
            continue
        logger.warning("Bootstrap: registered platform service '%s'", service_name)
        audit_service.emit(
            "service.create",
            actor_id="bootstrap",
            actor_type="service",
            target_id=service_name,
            target_type="service",
            status="success",
            allowed=True,
            details={
                "reason": "bootstrap_seed",
                "service_name": service_name,
                "description": description,
            },
        )


async def bootstrap_worker_bot(db: AsyncSession) -> None:
    """Завести бота воркера из `WORKER_BOT_TOKEN` (если токен задан).

    Обеспечивает всю цепочку, нужную server_worker'у для аутентификации на
    internal-эндпоинтах server_service:

    * системный отдел `DBOS System`;
    * активный access этого отдела к `server_service`
      (без него introspect отфильтрует роль на INTERSECT'е allowed × dept);
    * системную роль `worker_bot@server_service`;
    * бота `server_worker` (`allowed_services=['server_service']`) с этой ролью;
    * bot-токен с хэшем `WORKER_BOT_TOKEN` (тот же `hash_opaque_token`, что
      валидирует introspect) — токен не генерируем, берём готовый из env.

    Идемпотентно и безопасно к гонке реплик: если токен с таким хэшем уже
    есть — no-op; вся провизия идёт одной транзакцией, на IntegrityError
    (выиграла другая реплика) откатывается целиком. Пустой `WORKER_BOT_TOKEN`
    — шаг пропускается (dev/test без воркера).
    """
    settings = get_settings()
    token = (settings.worker_bot_token or "").strip()
    if not token:
        return

    from src.models.bot_service_role import BotServiceRole
    from src.repositories.bot_roles import BotRoleRepository
    from src.repositories.bot_tokens import BotTokenRepository
    from src.repositories.bots import BotRepository
    from src.repositories.departments import DepartmentRepository
    from src.repositories.service_role_definitions import (
        ServiceRoleDefinitionRepository,
    )
    from src.utils.ids import bot_service_role_id

    token_hash = hash_opaque_token(token)

    bot_token_repo = BotTokenRepository(db)
    # Идемпотентность по токену: если bot-токен с таким хэшем уже есть — бот и
    # роль заведены прошлым стартом, выходим без изменений.
    if await bot_token_repo.get_active_by_hash(token_hash) is not None:
        return

    dept_repo = DepartmentRepository(db)
    role_def_repo = ServiceRoleDefinitionRepository(db)
    bot_repo = BotRepository(db)
    bot_role_repo = BotRoleRepository(db)

    try:
        # 1. Системный отдел.
        dept = await dept_repo.get_by_name(SYSTEM_DEPARTMENT_NAME)
        if dept is None:
            dept = await dept_repo.create(SYSTEM_DEPARTMENT_NAME)

        # 2. Активный access отдела к server_service.
        access = await dept_repo.get_access(dept.id, WORKER_BOT_SERVICE)
        if access is None:
            await dept_repo.grant_access(dept.id, WORKER_BOT_SERVICE, granted_by="bootstrap")
        elif not access.is_active:
            access.is_active = True
            access.revoked_at = None
            access.revoked_by = None
            await db.flush()

        # 3. Системная роль worker_bot@server_service.
        role_def = await role_def_repo.get(dept.id, WORKER_BOT_SERVICE, WORKER_BOT_ROLE)
        if role_def is None:
            await role_def_repo.create(
                department_id=dept.id,
                service_name=WORKER_BOT_SERVICE,
                role_name=WORKER_BOT_ROLE,
                description="Worker bot least-privilege role (internal callbacks)",
                created_by="bootstrap",
                is_system=True,
            )

        # 4. Сам бот.
        bot = await bot_repo.first_by_name(WORKER_BOT_NAME)
        if bot is None:
            bot = await bot_repo.create(
                name=WORKER_BOT_NAME,
                department_id=dept.id,
                allowed_services=[WORKER_BOT_SERVICE],
                description="Bootstrap: bot для server_worker'а",
                created_by="bootstrap",
            )

        # 5. Привязка роли к боту.
        if WORKER_BOT_ROLE not in await bot_role_repo.get_roles_by_service(
            bot.id, WORKER_BOT_SERVICE
        ):
            db.add(
                BotServiceRole(
                    id=bot_service_role_id(),
                    bot_id=bot.id,
                    service_name=WORKER_BOT_SERVICE,
                    role=WORKER_BOT_ROLE,
                    assigned_by="bootstrap",
                )
            )
            await db.flush()

        # 6. Bot-токен с хэшем WORKER_BOT_TOKEN. Без expires_at: срок жизни
        # управляется секретом в k8s / ротацией, а не TTL в БД.
        await bot_token_repo.create(
            bot_id=bot.id,
            name="bootstrap",
            token_hash=token_hash,
            token_prefix=token[:TOKEN_PREFIX_LEN],
            expires_at=None,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return

    logger.warning(
        "Bootstrap: provisioned worker bot '%s' (dept '%s', role %s@%s) "
        "from WORKER_BOT_TOKEN",
        WORKER_BOT_NAME,
        SYSTEM_DEPARTMENT_NAME,
        WORKER_BOT_ROLE,
        WORKER_BOT_SERVICE,
    )
    audit_service.emit(
        "bot.create",
        actor_id="bootstrap",
        actor_type="service",
        target_id=bot.id,
        target_type="bot",
        status="success",
        allowed=True,
        details={
            "reason": "bootstrap_seed",
            "name": WORKER_BOT_NAME,
            "department_id": bot.department_id,
            "allowed_services": [WORKER_BOT_SERVICE],
            "role": f"{WORKER_BOT_ROLE}@{WORKER_BOT_SERVICE}",
        },
    )
    audit_service.emit(
        "bot.token_create",
        actor_id="bootstrap",
        actor_type="service",
        target_id=bot.id,
        target_type="bot",
        status="success",
        allowed=True,
        details={
            "reason": "bootstrap_seed",
            "bot_id": bot.id,
            "bot_name": WORKER_BOT_NAME,
            "token_name": "bootstrap",
            "token_prefix": token[:TOKEN_PREFIX_LEN],
        },
    )
