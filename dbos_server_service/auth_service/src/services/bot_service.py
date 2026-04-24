"""Bot and bot-token workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.core.security import generate_bot_token
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.services import ServiceRepository
from src.repositories.users import UserRepository
from src.schemas.bots import (
    BotCreate,
    BotResponse,
    BotTokenCreateResponse,
    BotTokenListItem,
    BotUpdate,
)
from src.services import audit_service


async def _validate_bot_services(dept_repo: DepartmentRepository, department_id: str, requested: list[str]) -> None:
    """Raise if any requested service is not accessible to the department."""
    allowed = set(await dept_repo.list_active_services(department_id))
    forbidden = set(requested) - allowed
    if forbidden:
        raise AuthorizationError(
            error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
            message="Bot cannot be granted services the department has no access to",
            details={"forbidden_services": sorted(forbidden)},
        )


def _to_response(bot) -> BotResponse:
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
) -> BotResponse:
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    bot_repo = BotRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != data.department_id:
            raise AuthorizationError(error_code="BOT_CREATION_FORBIDDEN", message="department_admin can only create bots in their own department")

    dept = await dept_repo.get_by_id(data.department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    await _validate_bot_services(dept_repo, data.department_id, data.allowed_services)

    bot = await bot_repo.create(
        name=data.name,
        department_id=data.department_id,
        allowed_services=data.allowed_services,
        description=data.description,
        created_by=actor_id,
    )
    await db.commit()
    audit_service.emit("bot.create", actor_id, target_id=bot.id, target_type="bot", request_id=request_id)
    return _to_response(bot)


async def list_bots(db: AsyncSession, actor_id: str, actor_role: str | None, department_id: str | None = None, request_id: str | None = None) -> list[BotResponse]:
    user_repo = UserRepository(db)
    bot_repo = BotRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        dept_id = actor.department_id if actor else None
        bots = await bot_repo.list_by_department(dept_id) if dept_id else []
    elif department_id:
        bots = await bot_repo.list_by_department(department_id)
    else:
        bots = await bot_repo.list_all()

    audit_service.emit("bot.list", actor_id, status="success", allowed=True, request_id=request_id)
    return [_to_response(b) for b in bots]


async def update_bot(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    bot_id: str,
    data: BotUpdate,
    request_id: str | None = None,
) -> BotResponse:
    user_repo = UserRepository(db)
    bot_repo = BotRepository(db)

    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != bot.department_id:
            raise AuthorizationError(error_code="BOT_UPDATE_FORBIDDEN", message="Cannot update bot outside your department")

    updates = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    if "allowed_services" in updates:
        dept_repo = DepartmentRepository(db)
        await _validate_bot_services(dept_repo, bot.department_id, updates["allowed_services"])
    if "status" in updates:
        updates["is_active"] = updates["status"] == "active"
    await bot_repo.update(bot, **updates)
    await db.commit()
    audit_service.emit("bot.update", actor_id, target_id=bot_id, target_type="bot", request_id=request_id)
    return _to_response(bot)


async def create_bot_token(
    db: AsyncSession,
    actor_id: str,
    bot_id: str,
    name: str,
    expires_at=None,
    request_id: str | None = None,
) -> BotTokenCreateResponse:
    bot_repo = BotRepository(db)
    token_repo = BotTokenRepository(db)

    bot = await bot_repo.get_by_id(bot_id)
    if bot is None:
        raise NotFoundError(error_code="BOT_NOT_FOUND", message="Bot not found")

    if await token_repo.exists_name(bot_id, name):
        raise ConflictError(error_code="TOKEN_NAME_ALREADY_EXISTS", message=f"Token '{name}' already exists")

    raw, prefix, token_hash = generate_bot_token()
    token = await token_repo.create(
        bot_id=bot_id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=expires_at,
    )
    await db.commit()
    audit_service.emit("bot.token_create", actor_id, target_id=token.id, target_type="bot_token", details={"bot_id": bot_id}, request_id=request_id)
    return BotTokenCreateResponse(token_id=token.id, token=raw, name=token.name, expires_at=token.expires_at)


async def list_bot_tokens(db: AsyncSession, bot_id: str, actor_id: str | None = None, request_id: str | None = None) -> list[BotTokenListItem]:
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
    audit_service.emit("bot.token_list", actor_id, target_id=bot_id, target_type="bot", status="success", allowed=True, request_id=request_id)
    return result


async def revoke_bot_token(
    db: AsyncSession,
    actor_id: str,
    bot_id: str,
    token_id: str,
    request_id: str | None = None,
) -> None:
    token_repo = BotTokenRepository(db)
    token = await token_repo.get_by_id(token_id)

    if token is None or token.bot_id != bot_id:
        raise NotFoundError(error_code="BOT_TOKEN_NOT_FOUND", message="Bot token not found")

    if token.revoked_at is not None:
        raise ConflictError(error_code="BOT_TOKEN_ALREADY_REVOKED", message="Token is already revoked", details={"token_id": token_id})

    await token_repo.revoke(token)
    await db.commit()
    audit_service.emit("bot.token_revoke", actor_id, target_id=token_id, target_type="bot_token", request_id=request_id)
