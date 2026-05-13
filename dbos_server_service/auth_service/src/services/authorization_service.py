"""Authorization and service-access workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SubjectType
from src.core.security import decode_access_token, hash_opaque_token
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.roles import RoleRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.authorization import IntrospectResponse, ServiceAccessResponse
from src.services import audit_service
from src.utils.time import is_expired, utcnow


async def introspect(db: AsyncSession, token: str, request_id: str | None = None) -> IntrospectResponse:
    """Validate any token type (JWT / PAT / bot token) and return subject context."""

    # 1. Try JWT access token
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        dept_id = payload.get("department_id")
        audit_service.emit(
            "token.introspect",
            sub,
            actor_type="user",
            department_id=dept_id,
            status="success",
            allowed=True,
            details={
                "token_type": "jwt",
                "username": payload.get("username"),
                "platform_role": payload.get("platform_role"),
                "allowed_services": payload.get("allowed_services", []),
                "exp": payload.get("exp"),
            },
            request_id=request_id,
        )
        return IntrospectResponse(
            active=True,
            subject_type=SubjectType.USER,
            sub=sub,
            department_id=dept_id,
            allowed_services=payload.get("allowed_services", []),
            service_roles=payload.get("service_roles", {}),
            exp=payload.get("exp"),
        )
    except Exception:
        pass

    # 2. Try PAT
    token_hash = hash_opaque_token(token)
    token_repo = TokenRepository(db)
    pat = await token_repo.get_active_by_hash(token_hash)
    if pat is not None:
        if pat.expires_at and is_expired(pat.expires_at):
            audit_service.emit(
                "token.introspect", pat.user_id, status="failure", allowed=False,
                details={
                    "token_type": "pat",
                    "reason": "expired",
                    "pat_id": pat.id,
                    "pat_name": pat.name,
                    "token_prefix": pat.token_prefix,
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
        await token_repo.touch(pat)
        user_repo = UserRepository(db)
        role_repo = RoleRepository(db)
        dept_repo = DepartmentRepository(db)
        user = await user_repo.get_by_id(pat.user_id)
        if user is None:
            return IntrospectResponse(active=False)
        allowed = await dept_repo.list_active_services(user.department_id)
        effective_services = [s for s in pat.allowed_services if s in allowed]
        roles = await role_repo.get_all_roles(user.id)
        effective_roles = {k: v for k, v in roles.items() if k in effective_services}
        await db.commit()
        audit_service.emit(
            "token.introspect",
            user.id,
            actor_type="user",
            department_id=user.department_id,
            target_id=pat.id,
            target_type="pat",
            status="success",
            allowed=True,
            details={
                "token_type": "pat",
                "username": user.username,
                "pat_id": pat.id,
                "pat_name": pat.name,
                "token_prefix": pat.token_prefix,
                "effective_services": effective_services,
            },
            request_id=request_id,
        )
        return IntrospectResponse(
            active=True,
            subject_type=SubjectType.USER,
            sub=user.id,
            department_id=user.department_id,
            allowed_services=effective_services,
            service_roles=effective_roles,
        )

    # 3. Try bot token
    bot_token_repo = BotTokenRepository(db)
    bot_token = await bot_token_repo.get_active_by_hash(token_hash)
    if bot_token is not None:
        if bot_token.expires_at and is_expired(bot_token.expires_at):
            audit_service.emit(
                "token.introspect", bot_token.bot_id, actor_type="bot",
                status="failure", allowed=False,
                details={
                    "token_type": "bot_token",
                    "reason": "expired",
                    "bot_token_id": bot_token.id,
                    "token_name": bot_token.name,
                    "token_prefix": bot_token.token_prefix,
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
        await bot_token_repo.touch(bot_token)
        bot_repo = BotRepository(db)
        bot = await bot_repo.get_by_id(bot_token.bot_id)
        if bot is None or not bot.is_active:
            audit_service.emit(
                "token.introspect", bot_token.bot_id, actor_type="bot",
                status="failure", allowed=False,
                details={
                    "token_type": "bot_token",
                    "reason": "bot_inactive",
                    "bot_id": bot_token.bot_id,
                    "bot_token_id": bot_token.id,
                    "token_prefix": bot_token.token_prefix,
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
        dept_repo = DepartmentRepository(db)
        dept_services = set(await dept_repo.list_active_services(bot.department_id))
        effective_services = [s for s in bot.allowed_services if s in dept_services]
        await db.commit()
        audit_service.emit(
            "token.introspect",
            bot.id,
            actor_type="bot",
            department_id=bot.department_id,
            target_id=bot_token.id,
            target_type="bot_token",
            status="success",
            allowed=True,
            details={
                "token_type": "bot_token",
                "bot_name": bot.name,
                "bot_token_id": bot_token.id,
                "token_name": bot_token.name,
                "token_prefix": bot_token.token_prefix,
                "effective_services": effective_services,
            },
            request_id=request_id,
        )
        return IntrospectResponse(
            active=True,
            subject_type=SubjectType.BOT,
            sub=bot.id,
            department_id=bot.department_id,
            allowed_services=effective_services,
            service_roles={},
        )

    audit_service.emit(
        "token.introspect",
        None,
        status="failure",
        allowed=False,
        details={"reason": "no_valid_token", "token_length": len(token) if token else 0},
        request_id=request_id,
    )
    return IntrospectResponse(active=False)


async def check_service_access(
    db: AsyncSession,
    token: str,
    service_name: str,
    request_id: str | None = None,
) -> ServiceAccessResponse:
    result = await introspect(db, token, request_id=request_id)
    if not result.active:
        return ServiceAccessResponse(allowed=False)

    dept_repo = DepartmentRepository(db)
    if result.department_id and not await dept_repo.has_active_access(result.department_id, service_name):
        audit_service.emit(
            "service.access_check",
            result.sub,
            department_id=result.department_id,
            target_id=service_name,
            target_type="service",
            status="denied",
            allowed=False,
            details={
                "reason": "department_no_access",
                "service_name": service_name,
                "subject_id": result.sub,
                "department_id": result.department_id,
            },
            request_id=request_id,
        )
        return ServiceAccessResponse(allowed=False, department_id=result.department_id, service_roles=[])

    if service_name not in (result.allowed_services or []):
        audit_service.emit(
            "service.access_check",
            result.sub,
            department_id=result.department_id,
            target_id=service_name,
            target_type="service",
            status="denied",
            allowed=False,
            details={
                "reason": "service_not_in_token",
                "service_name": service_name,
                "subject_id": result.sub,
                "allowed_services": list(result.allowed_services or []),
            },
            request_id=request_id,
        )
        return ServiceAccessResponse(allowed=False, department_id=result.department_id, service_roles=[])

    roles = result.service_roles.get(service_name, [])
    audit_service.emit(
        "service.access_check",
        result.sub,
        department_id=result.department_id,
        target_id=service_name,
        target_type="service",
        status="success",
        allowed=True,
        details={
            "service_name": service_name,
            "subject_id": result.sub,
            "roles": list(roles),
        },
        request_id=request_id,
    )
    return ServiceAccessResponse(allowed=True, department_id=result.department_id, service_roles=roles)
