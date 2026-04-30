"""Authentication and session workflows."""

from datetime import timedelta, timezone

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import UserStatus
from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from src.repositories.bans import BanRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.sessions import SessionRepository
from src.repositories.users import UserRepository
from src.schemas.auth import IdentityContext, LoginResponse, RefreshResponse
from src.services import audit_service
from src.utils.time import expires_at, is_expired, utcnow

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


def _merge_permissions(
    dept_services: list[str],
    direct_roles: dict[str, list[str]],
    group_services: list[str],
    group_roles: dict[str, list[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Merge dept/group service access and direct/group roles."""
    services = list(set(dept_services + group_services))
    merged: dict[str, list[str]] = {}
    for svc, roles in direct_roles.items():
        merged.setdefault(svc, []).extend(roles)
    for svc, roles in group_roles.items():
        merged.setdefault(svc, []).extend(roles)
    return services, {svc: list(set(roles)) for svc, roles in merged.items()}


def _build_identity(user, dept_name: str | None, allowed_services: list[str], service_roles: dict) -> IdentityContext:
    is_account_admin = user.platform_role == "account_admin"
    return IdentityContext(
        user_id=user.id,
        username=user.username,
        department_id=user.department_id,
        department_name=dept_name,
        allowed_services=[] if is_account_admin else allowed_services,
        service_roles={} if is_account_admin else service_roles,
        is_banned=user.status == UserStatus.BANNED,
        platform_role=user.platform_role,
    )


def _build_access_token(user, allowed_services: list[str], service_roles: dict) -> str:
    settings = get_settings()
    return create_access_token(
        payload={
            "sub": user.id,
            "username": user.username,
            "department_id": user.department_id,
            "platform_role": user.platform_role,
            "allowed_services": allowed_services,
            "service_roles": service_roles,
        },
        expires_delta=timedelta(minutes=settings.access_token_ttl_minutes),
    )


async def login(
    db: AsyncSession,
    username: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> LoginResponse:
    settings = get_settings()
    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)
    ban_repo = BanRepository(db)

    user = await user_repo.get_by_username(username)
    if user is None:
        audit_service.emit("user.login", None, status="failure", allowed=False, details={"username": username, "reason": "user_not_found"}, request_id=request_id)
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid username or password")

    if user.locked_until and is_expired(user.locked_until):
        await user_repo.reset_failed_attempts(user)

    if user.locked_until and not is_expired(user.locked_until):
        locked_dt = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=timezone.utc)
        retry_secs = int((locked_dt - utcnow()).total_seconds())
        audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "account_locked"}, request_id=request_id)
        raise AuthorizationError(
            error_code="ACCOUNT_TEMPORARILY_LOCKED",
            message="Account is temporarily locked",
            details={"retry_after_seconds": retry_secs},
            http_status=429,
        )

    if user.status == UserStatus.BANNED:
        audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "banned"}, request_id=request_id)
        raise AuthorizationError(error_code="USER_BANNED", message="User is banned")

    if user.status == UserStatus.BLOCKED:
        audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "blocked"}, request_id=request_id)
        raise AuthorizationError(error_code="USER_BLOCKED", message="User is blocked")

    if not verify_password(password, user.password_hash):
        await user_repo.increment_failed_attempts(user)
        if user.failed_login_attempts >= _MAX_FAILED_ATTEMPTS:
            user.locked_until = expires_at(minutes=_LOCKOUT_MINUTES)
            await db.flush()
        audit_service.emit("user.login", user.id, status="failure", allowed=False, details={"reason": "invalid_password", "attempts": user.failed_login_attempts}, request_id=request_id)
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid username or password")

    await user_repo.reset_failed_attempts(user)

    dept_services = await dept_repo.list_active_services(user.department_id) if user.department_id else []
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(dept_services, direct_roles, group_services, group_roles)
    dept = await dept_repo.get_by_id(user.department_id) if user.department_id else None

    raw_refresh, refresh_hash = generate_refresh_token()
    refresh_expires = expires_at(days=settings.refresh_token_ttl_days)
    await session_repo.create(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=refresh_expires,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    access_token = _build_access_token(user, allowed_services, service_roles)
    await db.commit()

    audit_service.emit("user.login", user.id, status="success", request_id=request_id)
    return LoginResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        identity=_build_identity(user, dept.display_name if dept else None, allowed_services, service_roles),
    )


async def refresh(db: AsyncSession, raw_refresh_token: str, request_id: str | None = None) -> RefreshResponse:
    settings = get_settings()
    session_repo = SessionRepository(db)
    user_repo = UserRepository(db)
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)

    token_hash = hash_refresh_token(raw_refresh_token)
    sess = await session_repo.get_active_by_token_hash(token_hash)

    if sess is None:
        old_sess = await session_repo.get_by_token_hash(token_hash)
        if old_sess:
            await session_repo.mark_suspicious(old_sess)
            await session_repo.revoke_all_for_user(old_sess.user_id)
            await db.commit()
            audit_service.emit("token.refresh_reuse", old_sess.user_id, status="failure", allowed=False, details={"session_id": old_sess.id}, request_id=request_id)
        raise AuthenticationError(error_code="REFRESH_TOKEN_INVALID", message="Invalid refresh token")

    if is_expired(sess.expires_at):
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthenticationError(error_code="REFRESH_TOKEN_EXPIRED", message="Refresh token expired")

    user = await user_repo.get_by_id(sess.user_id)
    if user is None:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_NOT_FOUND", message="User not found")
    if user.status == UserStatus.BANNED:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_BANNED", message="User is banned")
    if user.status == UserStatus.BLOCKED:
        await session_repo.revoke(sess)
        await db.commit()
        raise AuthorizationError(error_code="USER_BLOCKED", message="User is blocked")

    dept_services = await dept_repo.list_active_services(user.department_id)
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(dept_services, direct_roles, group_services, group_roles)

    new_raw, new_hash = generate_refresh_token()
    new_expires = expires_at(days=settings.refresh_token_ttl_days)
    await session_repo.rotate(sess, new_hash, new_expires)

    access_token = _build_access_token(user, allowed_services, service_roles)
    await db.commit()

    audit_service.emit("user.refresh", user.id, status="success", request_id=request_id)
    return RefreshResponse(
        access_token=access_token,
        refresh_token=new_raw,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def logout(db: AsyncSession, raw_refresh_token: str, request_id: str | None = None) -> None:
    session_repo = SessionRepository(db)
    token_hash = hash_refresh_token(raw_refresh_token)
    sess = await session_repo.get_active_by_token_hash(token_hash)
    if sess:
        user_id = sess.user_id
        await session_repo.revoke(sess)
        await db.commit()
        audit_service.emit("user.logout", user_id, status="success", request_id=request_id)


async def get_identity(db: AsyncSession, user_id: str, request_id: str | None = None) -> IdentityContext:
    user_repo = UserRepository(db)
    role_repo = RoleRepository(db)
    dept_repo = DepartmentRepository(db)
    group_repo = GroupRepository(db)

    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise AuthenticationError(error_code="USER_NOT_FOUND", message="User not found")

    dept_services = await dept_repo.list_active_services(user.department_id) if user.department_id else []
    direct_roles = await role_repo.get_all_roles(user.id)
    group_services = await group_repo.list_active_services_for_user(user.id)
    group_roles = await group_repo.get_roles_for_user(user.id)
    allowed_services, service_roles = _merge_permissions(dept_services, direct_roles, group_services, group_roles)
    dept = await dept_repo.get_by_id(user.department_id) if user.department_id else None

    audit_service.emit("user.me", user_id, status="success", allowed=True, request_id=request_id)
    return _build_identity(user, dept.display_name if dept else None, allowed_services, service_roles)


def _decode(token: str) -> dict:
    from src.core.security import decode_access_token
    try:
        return decode_access_token(token)
    except Exception:
        raise AuthenticationError(error_code="ACCESS_TOKEN_EXPIRED", message="Invalid or expired token")
