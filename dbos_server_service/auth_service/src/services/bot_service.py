"""Бизнес-логика ботов: CRUD bot accounts + выдача/отзыв bot-токенов + service-роли."""

from datetime import timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import BOT_TOKEN_TTL_SECONDS, PlatformRole
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.core.security import generate_bot_token
from src.repositories.bot_roles import BotRoleRepository
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.users import UserRepository
from src.schemas.bots import (
    BotCreate,
    BotResponse,
    BotRoleResponse,
    BotTokenCreateResponse,
    BotTokenListItem,
    BotUpdate,
)
from src.services import audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.utils.pagination import PaginationParams
from src.utils.time import utcnow


async def _resolve_actor_dept(
    db: AsyncSession,
    actor_id: str | None,
    actor_department_id: str | None,
) -> str | None:
    """Вернуть dept_id актора без лишнего SELECT'а, если он уже известен из identity.

    `actor_department_id` передаёт endpoint из `IdentityContext.department_id` —
    middleware уже сходил в БД при загрузке identity, второй раз дёргать
    users не нужно. Если параметр не передан (старые caller'ы / прямые
    вызовы из сервисов) — фолбэк на SELECT.
    """
    if actor_department_id is not None or actor_id is None:
        return actor_department_id
    user_repo = UserRepository(db)
    actor = await user_repo.get_by_id(actor_id)
    return actor.department_id if actor else None


async def _validate_bot_services(dept_repo: DepartmentRepository, department_id: str, requested: list[str]) -> None:
    """Кидает AuthorizationError если запрошенный сервис недоступен отделу."""
    allowed = set(await dept_repo.list_active_services(department_id))
    forbidden = set(requested) - allowed
    if forbidden:
        raise AuthorizationError(
            error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
            message="Bot cannot be granted services the department has no access to",
            details={"forbidden_services": sorted(forbidden)},
        )


def _to_response(bot) -> BotResponse:
    """ORM-бот → BotResponse DTO."""
    return BotResponse(
        bot_id=bot.id,
        name=bot.name,
        department_id=bot.department_id,
        allowed_services=bot.allowed_services,
        description=bot.description,
        status=bot.status,
        is_active=bot.is_active,
        created_at=bot.created_at,
    )


async def create_bot(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    data: BotCreate,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> BotResponse:
    """Создать бота. department_admin может создавать только в своём отделе."""
    dept_repo = DepartmentRepository(db)
    bot_repo = BotRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor_department_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
        if actor_department_id is not None and actor_department_id != data.department_id:
            raise AuthorizationError(error_code="BOT_CREATION_FORBIDDEN", message="department_admin can only create bots in their own department")

    dept = await dept_repo.get_by_id(data.department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    await _validate_bot_services(dept_repo, data.department_id, data.allowed_services)

    # bot.name глобально-уникален: docker basic-auth ищет по имени без
    # department-фильтра. Pre-check 409 — чище IntegrityError'а из БД.
    existing = await bot_repo.first_by_name(data.name)
    if existing is not None:
        raise ConflictError(
            error_code="BOT_NAME_TAKEN",
            message=f"Bot with name '{data.name}' already exists",
        )

    bot = await bot_repo.create(
        name=data.name,
        department_id=data.department_id,
        allowed_services=data.allowed_services,
        description=data.description,
        created_by=actor_id,
    )
    await db.commit()
    audit_service.emit(
        "bot.create", actor_id, target_id=bot.id, target_type="bot",
        request_id=request_id,
        details={
            "name": data.name,
            "department_id": data.department_id,
            "department_name": dept.display_name,
            "allowed_services": list(data.allowed_services),
            "description": data.description,
        },
    )
    return _to_response(bot)


async def list_bots(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str | None = None,
    pagination: PaginationParams | None = None,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> tuple[list[BotResponse], int]:
    """Список ботов с учётом scope-а смотрящего (страница).

    Возвращает `(страница, total)`. `total` считается в рамках того же
    scope-фильтра, что и сама страница.
    """
    pagination = pagination or PaginationParams()
    bot_repo = BotRepository(db)

    effective_department_id: str | None
    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor_department_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
        dept_id = actor_department_id
        effective_department_id = dept_id
        if dept_id:
            bots = await bot_repo.list_by_department(
                dept_id, limit=pagination.limit, offset=pagination.offset
            )
            total = await bot_repo.count_by_department(dept_id)
        else:
            bots, total = [], 0
    elif department_id:
        effective_department_id = department_id
        bots = await bot_repo.list_by_department(
            department_id, limit=pagination.limit, offset=pagination.offset
        )
        total = await bot_repo.count_by_department(department_id)
    else:
        effective_department_id = None
        bots = await bot_repo.list_all(limit=pagination.limit, offset=pagination.offset)
        total = await bot_repo.count_all()

    count_disabled = sum(1 for b in bots if not b.is_active)
    audit_service.emit(
        "bot.list", actor_id, status="success", allowed=True, request_id=request_id,
        details={
            "count": len(bots),
            "count_disabled": count_disabled,
            "total": total,
            "filter_department_id": department_id,
            "filter_department_id_requested": department_id,
            "filter_department_id_effective": effective_department_id,
            "scope": "department" if (actor_role == PlatformRole.DEPARTMENT_ADMIN or department_id) else "all",
        },
    )
    return [_to_response(b) for b in bots], total


async def update_bot(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    data: BotUpdate,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> BotResponse:
    """Patch бота. department_admin — только в своём отделе."""
    bot_repo = BotRepository(db)

    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
        # `actor_dept_id is None` для DEPARTMENT_ADMIN — data integrity bug:
        # такая identity в принципе невалидна, и пускать её через guard
        # нельзя (None != bot.department_id корректно отдаёт 403).
        if actor_dept_id != bot.department_id:
            # Симметрия с прочими bot-функциями: cross-tenant попытка
            # светится в audit как failure (а не теряется в 500), сохраняя
            # стабильный error_code BOT_UPDATE_FORBIDDEN для API-контракта.
            audit_service.emit(
                "bot.update", actor_id, status="failure", allowed=False,
                target_id=bot.id, target_type="bot",
                request_id=request_id,
                details={
                    "reason": "cross_tenant_bot",
                    "bot_id": bot.id,
                    "bot_department_id": bot.department_id,
                    "actor_department_id": actor_dept_id,
                },
            )
            raise AuthorizationError(error_code="BOT_UPDATE_FORBIDDEN", message="Cannot update bot outside your department")

    updates = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    removed_services: list[str] = []
    if "allowed_services" in updates:
        dept_repo = DepartmentRepository(db)
        await _validate_bot_services(dept_repo, bot.department_id, updates["allowed_services"])
        # Вычисляем сервисы, выкинутые из allowed_services. Любые BotServiceRole
        # на них становятся бесхозными: introspect отфильтрует их через
        # пересечение с allowed_services, а GET /bots/{id}/roles до фикса
        # отдавал их как призраков. Чистим явно, чтобы и каталог ролей был
        # консистентен, и аудиту было что эмитнуть.
        old_set = set(bot.allowed_services or [])
        new_set = set(updates["allowed_services"])
        removed_services = sorted(old_set - new_set)
    if "name" in updates and updates["name"] != bot.name:
        # Зеркало create_bot: имя глобально-уникально, иначе кто-то перетрёт
        # чужого бота для целей docker basic-auth.
        existing = await bot_repo.first_by_name(updates["name"])
        if existing is not None and existing.id != bot.id:
            raise ConflictError(
                error_code="BOT_NAME_TAKEN",
                message=f"Bot with name '{updates['name']}' already exists",
            )
    # `is_active` — derived от `status` (см. ORM-модель: оба поля живут рядом
    # из legacy-времён). В БД пишем оба, но в audit-details показываем только
    # то, что прислал caller, чтобы SIEM/оператор не видел "лишнего" поля,
    # которого в payload'е не было.
    db_updates = dict(updates)
    if "status" in db_updates:
        db_updates["is_active"] = db_updates["status"] == "active"
    await bot_repo.update(bot, **db_updates)
    removed_role_count = 0
    if removed_services:
        role_repo = BotRoleRepository(db)
        removed_role_count = await role_repo.delete_for_bot_services(bot.id, removed_services)
    await db.commit()
    # allowed_services / is_active / status / name могут влиять на ответ introspect'а —
    # без сброса бот ходит со старыми правами до истечения identity TTL (~5s).
    _invalidate_identity_cache(bot.id)
    audit_service.emit(
        "bot.update", actor_id, target_id=bot_id, target_type="bot",
        request_id=request_id,
        details={
            "bot_name": bot.name,
            "department_id": bot.department_id,
            "changes": updates,
            "fields_changed": sorted(updates.keys()),
        },
    )
    if removed_services:
        audit_service.emit(
            "bot.roles_purged_on_services_narrowed",
            actor_id,
            target_id=bot_id,
            target_type="bot",
            request_id=request_id,
            details={
                "bot_id": bot.id,
                "bot_name": bot.name,
                "department_id": bot.department_id,
                "removed_services": removed_services,
                "removed_role_count": removed_role_count,
            },
        )
    return _to_response(bot)


async def create_bot_token(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    name: str,
    expires_at=None,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> BotTokenCreateResponse:
    """Создать новый bot-токен. Raw возвращается один раз — больше нигде не покажем.

    Уникальность `name` держим только в рамках **активных** токенов бота:
    после revoke имя освобождается, dept_admin может пересоздать токен
    с прежним name (штатный flow ротации раз в полгода).
    """
    bot_repo = BotRepository(db)
    token_repo = BotTokenRepository(db)

    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.token_create",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
        extra_details={"token_name": name},
    )

    if await token_repo.exists_name(bot_id, name):
        raise ConflictError(error_code="TOKEN_NAME_ALREADY_EXISTS", message=f"Token '{name}' already exists")

    # Bot-токены живут 6 месяцев по умолчанию. Если caller передал явный
    # expires_at — уважаем его (валидируем aware/naive и future-ness),
    # иначе ставим now + 6mo. Бессрочные bot-токены запрещены: их сложно
    # ротировать, dept_admin'у проще перевыпустить раз в полгода.
    if expires_at is None:
        effective_expires_at = utcnow() + timedelta(seconds=BOT_TOKEN_TTL_SECONDS)
    else:
        exp_dt = (
            expires_at if expires_at.tzinfo is not None
            else expires_at.replace(tzinfo=timezone.utc)
        )
        if exp_dt <= utcnow():
            raise DomainValidationError(
                error_code="INVALID_TOKEN_EXPIRY",
                message="expires_at must be in the future",
                details={"expires_at": exp_dt.isoformat()},
            )
        effective_expires_at = exp_dt

    raw, prefix, token_hash = generate_bot_token()
    token = await token_repo.create(
        bot_id=bot_id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=effective_expires_at,
    )
    await db.commit()
    # Plaintext bot-токен в audit не кладём: SOC получает событие создания с
    # token_id/token_prefix, а raw отдаём только caller'у через response.
    audit_service.emit(
        "bot.token_create", actor_id, target_id=token.id, target_type="bot_token",
        details={
            "bot_id": bot_id,
            "bot_name": bot.name,
            "token_id": token.id,
            "token_name": name,
            "token_prefix": prefix,
            "expires_at": effective_expires_at.isoformat(),
        },
        request_id=request_id,
    )
    return BotTokenCreateResponse(token_id=token.id, token=raw, name=token.name, expires_at=token.expires_at)


async def list_bot_tokens(
    db: AsyncSession,
    bot_id: str,
    actor_id: str | None = None,
    actor_role: str | None = None,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> list[BotTokenListItem]:
    bot_repo = BotRepository(db)
    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.token_list",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
    )

    token_repo = BotTokenRepository(db)
    result = [
        BotTokenListItem(
            token_id=t.id,
            name=t.name,
            token_prefix=t.token_prefix,
            created_at=t.created_at,
            expires_at=t.expires_at,
            last_used_at=t.last_used_at,
            revoked_at=t.revoked_at,
        )
        for t in await token_repo.list_for_bot(bot_id)
    ]
    audit_service.emit(
        "bot.token_list", actor_id, target_id=bot_id, target_type="bot",
        status="success", allowed=True, request_id=request_id,
        details={"count": len(result)},
    )
    return result


def _require_can_manage_bot(actor_role: str | None, actor_dept_id: str | None, bot) -> None:
    """Гард: account_admin или department_admin отдела бота. Иначе AuthorizationError."""
    if actor_role == PlatformRole.ACCOUNT_ADMIN:
        return
    if actor_role == PlatformRole.DEPARTMENT_ADMIN and actor_dept_id == bot.department_id:
        return
    raise AuthorizationError(
        error_code="BOT_ROLE_MGMT_FORBIDDEN",
        message="account_admin or department_admin of the bot's department required",
    )


def _check_can_manage_bot_or_audit(
    *,
    action: str,
    actor_id: str | None,
    actor_role: str | None,
    actor_dept_id: str | None,
    bot,
    request_id: str | None,
    extra_details: dict | None = None,
) -> None:
    """То же, что `_require_can_manage_bot`, но эмитит `failure`-audit перед raise.

    SIEM по `status="failure"` отличит cross-tenant попытку от 500 — без этого
    `AuthorizationError` поднимался напрямую и денайды смешивались с server-error.
    Конвенция status'а — единая по auth_service (см. остальные emit'ы), отдельный
    `denied`-status для bot-зоны путал классификацию.
    """
    try:
        _require_can_manage_bot(actor_role, actor_dept_id, bot)
    except AuthorizationError:
        details = {
            "reason": "cross_tenant_bot",
            "bot_id": bot.id,
            "bot_department_id": bot.department_id,
            "actor_department_id": actor_dept_id,
        }
        if extra_details:
            details.update(extra_details)
        audit_service.emit(
            action, actor_id, status="failure", allowed=False,
            target_id=bot.id, target_type="bot",
            request_id=request_id, details=details,
        )
        raise


async def list_bot_roles(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> list[BotRoleResponse]:
    bot_repo = BotRepository(db)
    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.roles_list",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
    )

    role_repo = BotRoleRepository(db)
    roles_by_svc = await role_repo.get_all_roles(bot.id)
    audit_service.emit(
        "bot.roles_list", actor_id, target_id=bot_id, target_type="bot",
        request_id=request_id,
        details={"bot_name": bot.name, "service_count": len(roles_by_svc)},
    )
    return [BotRoleResponse(service_name=svc, roles=roles) for svc, roles in roles_by_svc.items()]


async def assign_bot_roles(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    service_name: str,
    roles: list[str],
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> BotRoleResponse:
    bot_repo = BotRepository(db)
    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.roles_assign",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
        extra_details={"service_name": service_name, "roles": list(roles)},
    )

    dept_repo = DepartmentRepository(db)
    if not await dept_repo.has_active_access(bot.department_id, service_name):
        raise AuthorizationError(
            error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
            message=f"Service '{service_name}' is not allowed for the bot's department",
            details={"service_name": service_name, "department_id": bot.department_id},
        )
    if service_name not in (bot.allowed_services or []):
        raise AuthorizationError(
            error_code="SERVICE_NOT_IN_BOT_ALLOWED",
            message=f"Service '{service_name}' is not in the bot's allowed_services",
            details={"service_name": service_name, "allowed_services": list(bot.allowed_services or [])},
        )

    role_def_repo = ServiceRoleDefinitionRepository(db)
    # Один SELECT всех активных ролей сервиса в отделе вместо N×exists.
    defined = {
        rd.role_name
        for rd in await role_def_repo.list_active(bot.department_id, service_name)
    }
    for role in roles:
        if role not in defined:
            raise DomainValidationError(
                error_code="INVALID_SERVICE_ROLE",
                message=(
                    f"Role '{role}' is not defined for service '{service_name}' "
                    f"in department '{bot.department_id}'"
                ),
                details={"service_name": service_name, "role": role},
            )

    role_repo = BotRoleRepository(db)
    await role_repo.set_roles(bot_id, service_name, roles, assigned_by=actor_id)
    await db.commit()
    audit_service.emit(
        "bot.roles_assign", actor_id, target_id=bot_id, target_type="bot",
        details={
            "bot_name": bot.name,
            "department_id": bot.department_id,
            "service_name": service_name,
            "roles": list(roles),
        },
        request_id=request_id,
    )
    return BotRoleResponse(service_name=service_name, roles=roles)


async def revoke_bot_roles(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    service_name: str,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> None:
    bot_repo = BotRepository(db)
    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.roles_revoke",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
        extra_details={"service_name": service_name},
    )

    role_repo = BotRoleRepository(db)
    await role_repo.clear_roles_for_service(bot_id, service_name)
    await db.commit()
    audit_service.emit(
        "bot.roles_revoke", actor_id, target_id=bot_id, target_type="bot",
        details={"bot_name": bot.name, "service_name": service_name},
        request_id=request_id,
    )


async def revoke_bot_token(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    token_id: str,
    request_id: str | None = None,
    actor_department_id: str | None = None,
) -> None:
    bot_repo = BotRepository(db)
    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    actor_dept_id = await _resolve_actor_dept(db, actor_id, actor_department_id)
    _check_can_manage_bot_or_audit(
        action="bot.token_revoke",
        actor_id=actor_id,
        actor_role=actor_role,
        actor_dept_id=actor_dept_id,
        bot=bot,
        request_id=request_id,
        extra_details={"token_id": token_id},
    )

    token_repo = BotTokenRepository(db)
    token = await token_repo.get_by_id(token_id)

    if token is None or token.bot_id != bot_id:
        raise NotFoundError(error_code="BOT_TOKEN_NOT_FOUND", message="Bot token not found")

    if token.revoked_at is not None:
        raise ConflictError(error_code="BOT_TOKEN_ALREADY_REVOKED", message="Token is already revoked", details={"token_id": token_id})

    await token_repo.revoke(token)
    await db.commit()
    audit_service.emit(
        "bot.token_revoke", actor_id, target_id=token_id, target_type="bot_token",
        request_id=request_id,
        details={
            "bot_id": bot_id,
            "token_name": token.name,
            "token_prefix": token.token_prefix,
        },
    )
