"""Docker registry token auth + per-department configuration.

Reference: https://distribution.github.io/distribution/spec/auth/token/

Docker registry config.yml:
  auth:
    token:
      realm: https://<host>/api/auth/v1/docker/token
      service: <DOCKER_REGISTRY_SERVICE>
      issuer: <DOCKER_REGISTRY_ISSUER>
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, NotFoundError
from src.core.security import hash_opaque_token, verify_password
from src.core.config import get_settings
from src.models.department_docker_registry import PULL_POLICY_ALL, PULL_POLICY_RESTRICTED
from src.repositories.bots import BotRepository
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.docker_registry import DockerRegistryRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.docker_registry import (
    DockerRegistryConfigCreate,
    DockerRegistryConfigResponse,
    DockerRegistryConfigUpdate,
    DockerTokenResponse,
)
from src.services import audit_service
from src.utils.time import is_expired, utcnow
from src.core.docker_jwt import sign_docker_token


def _cfg_to_response(cfg) -> DockerRegistryConfigResponse:
    return DockerRegistryConfigResponse(
        department_id=cfg.department_id,
        is_enabled=cfg.is_enabled,
        pull_policy=cfg.pull_policy,
        pull_user_ids=cfg.pull_user_ids,
        push_user_ids=cfg.push_user_ids,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


# ── Config management ─────────────────────────────────────────────────────────

async def create_or_replace_config(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    data: DockerRegistryConfigCreate,
    request_id: str | None = None,
) -> DockerRegistryConfigResponse:
    from src.core.constants import PlatformRole
    user_repo = UserRepository(db)
    dept_repo = DepartmentRepository(db)
    docker_repo = DockerRegistryRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only configure Docker registry for their own department",
            )

    dept = await dept_repo.get_by_id(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    if data.pull_policy == PULL_POLICY_RESTRICTED and not data.pull_user_ids:
        raise AuthorizationError(
            error_code="PULL_USERS_REQUIRED",
            message="pull_user_ids must not be empty when pull_policy is 'restricted'",
        )

    cfg = await docker_repo.get_by_department(department_id)
    if cfg is None:
        cfg = await docker_repo.create(
            department_id=department_id,
            pull_policy=data.pull_policy,
            pull_user_ids=data.pull_user_ids,
            push_user_ids=data.push_user_ids,
            created_by=actor_id,
        )
    else:
        await docker_repo.update(
            cfg,
            is_enabled=True,
            pull_policy=data.pull_policy,
            pull_user_ids=data.pull_user_ids,
            push_user_ids=data.push_user_ids,
        )

    await db.commit()
    audit_service.emit(
        "docker_registry.configure", actor_id, target_type="docker_registry",
        details={
            "department_id": department_id,
            "department_name": dept.display_name,
            "pull_policy": data.pull_policy,
            "pull_user_ids": list(data.pull_user_ids or []),
            "push_user_ids": list(data.push_user_ids or []),
            "pull_user_count": len(data.pull_user_ids or []),
            "push_user_count": len(data.push_user_ids or []),
        },
        request_id=request_id,
    )
    return _cfg_to_response(cfg)


async def update_config(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    data: DockerRegistryConfigUpdate,
    request_id: str | None = None,
) -> DockerRegistryConfigResponse:
    from src.core.constants import PlatformRole
    user_repo = UserRepository(db)
    docker_repo = DockerRegistryRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only update Docker registry for their own department",
            )

    cfg = await docker_repo.get_by_department(department_id)
    if cfg is None:
        raise NotFoundError(
            error_code="DOCKER_REGISTRY_NOT_CONFIGURED",
            message="Docker registry is not configured for this department",
        )

    updates = {k: v for k, v in data.model_dump(exclude_none=True).items()}

    new_policy = updates.get("pull_policy", cfg.pull_policy)
    new_pull_ids = updates.get("pull_user_ids", cfg.pull_user_ids)
    if new_policy == PULL_POLICY_RESTRICTED and not new_pull_ids:
        raise AuthorizationError(
            error_code="PULL_USERS_REQUIRED",
            message="pull_user_ids must not be empty when pull_policy is 'restricted'",
        )

    await docker_repo.update(cfg, **updates)
    await db.commit()
    await db.refresh(cfg)
    audit_service.emit(
        "docker_registry.update", actor_id, target_type="docker_registry",
        details={
            "department_id": department_id,
            "changes": updates,
            "fields_changed": sorted(updates.keys()),
        },
        request_id=request_id,
    )
    return _cfg_to_response(cfg)


async def get_config(db: AsyncSession, department_id: str, actor_id: str | None = None, request_id: str | None = None) -> DockerRegistryConfigResponse:
    docker_repo = DockerRegistryRepository(db)
    cfg = await docker_repo.get_by_department(department_id)
    if cfg is None:
        raise NotFoundError(
            error_code="DOCKER_REGISTRY_NOT_CONFIGURED",
            message="Docker registry is not configured for this department",
        )
    from src.services import audit_service
    audit_service.emit(
        "docker_registry.get_config", actor_id, department_id=department_id,
        target_type="docker_registry", status="success", allowed=True,
        details={
            "department_id": department_id,
            "is_enabled": cfg.is_enabled,
            "pull_policy": cfg.pull_policy,
        },
        request_id=request_id,
    )
    return _cfg_to_response(cfg)


async def delete_config(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str,
    request_id: str | None = None,
) -> None:
    from src.core.constants import PlatformRole
    user_repo = UserRepository(db)
    docker_repo = DockerRegistryRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="department_admin can only disable Docker registry for their own department",
            )

    cfg = await docker_repo.get_by_department(department_id)
    if cfg is None:
        raise NotFoundError(
            error_code="DOCKER_REGISTRY_NOT_CONFIGURED",
            message="Docker registry is not configured for this department",
        )

    await docker_repo.update(cfg, is_enabled=False)
    await db.commit()
    audit_service.emit(
        "docker_registry.disable", actor_id, target_type="docker_registry",
        details={"department_id": department_id, "previous_pull_policy": cfg.pull_policy},
        request_id=request_id,
    )


# ── Token issuance ────────────────────────────────────────────────────────────

def _parse_scope(scope_str: str) -> list[dict]:
    entries = []
    for part in scope_str.split():
        segments = part.split(":")
        if len(segments) >= 3:
            resource_type = segments[0]
            resource_name = ":".join(segments[1:-1])
            actions = segments[-1].split(",")
            entries.append({"type": resource_type, "name": resource_name, "actions": actions})
    return entries


async def _authenticate_subject(db: AsyncSession, username: str, password: str):
    """Authenticate via password, PAT, or bot token. Returns (subject_id, department_id)."""
    user_repo = UserRepository(db)
    token_repo = TokenRepository(db)
    bot_token_repo = BotTokenRepository(db)
    bot_repo = BotRepository(db)

    if password.startswith(PAT_PREFIX):
        pat = await token_repo.get_active_by_hash(hash_opaque_token(password))
        if pat and not (pat.expires_at and is_expired(pat.expires_at)):
            user = await user_repo.get_by_id(pat.user_id)
            if user and user.status == "active":
                return user.id, user.department_id
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")

    if password.startswith(BOT_TOKEN_PREFIX):
        bot_token = await bot_token_repo.get_active_by_hash(hash_opaque_token(password))
        if bot_token and not (bot_token.expires_at and is_expired(bot_token.expires_at)):
            bot = await bot_repo.get_by_id(bot_token.bot_id)
            if bot and bot.is_active:
                return bot.id, bot.department_id
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")

    user = await user_repo.get_by_username(username)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")
    if user.status != "active":
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Account is not active")
    return user.id, user.department_id


def _resolve_actions(cfg, subject_id: str, requested_actions: list[str]) -> list[str]:
    """Return the subset of requested actions the subject is actually allowed."""
    allowed = []

    if "pull" in requested_actions:
        if cfg.pull_policy == PULL_POLICY_ALL:
            allowed.append("pull")
        elif subject_id in cfg.pull_user_ids:
            allowed.append("pull")

    if "push" in requested_actions:
        if subject_id in cfg.push_user_ids:
            allowed.append("push")

    return allowed


async def issue_token(
    db: AsyncSession,
    username: str,
    password: str,
    service: str,
    scope: str,
    request_id: str | None = None,
) -> DockerTokenResponse:
    settings = get_settings()
    subject_id, department_id = await _authenticate_subject(db, username, password)

    docker_repo = DockerRegistryRepository(db)
    cfg = await docker_repo.get_by_department(department_id)

    if cfg is None or not cfg.is_enabled:
        raise AuthorizationError(
            error_code="DOCKER_ACCESS_DENIED",
            message="Docker registry is not enabled for this department",
        )

    requested_access = _parse_scope(scope)
    allowed_access = []
    for entry in requested_access:
        permitted = _resolve_actions(cfg, subject_id, entry["actions"])
        if permitted:
            allowed_access.append({**entry, "actions": permitted})

    now = datetime.now(timezone.utc)
    ttl = timedelta(minutes=settings.docker_token_ttl_minutes)
    exp = int((now + ttl).timestamp())

    token = sign_docker_token({
        "iss": settings.docker_registry_issuer,
        "sub": subject_id,
        "aud": service or settings.docker_registry_service,
        "iat": int(now.timestamp()),
        "exp": exp,
        "jti": uuid.uuid4().hex,
        "access": allowed_access,
    })

    from src.services import audit_service
    audit_service.emit(
        "docker.token_issued",
        subject_id,
        department_id=department_id,
        target_id=service or settings.docker_registry_service,
        target_type="docker_registry",
        status="success",
        allowed=True,
        details={
            "username": username,
            "service": service or settings.docker_registry_service,
            "requested_scope": scope,
            "requested_access": requested_access,
            "granted_access": allowed_access,
            "granted_action_count": sum(len(a["actions"]) for a in allowed_access),
            "ttl_seconds": int(ttl.total_seconds()),
        },
        request_id=request_id,
    )
    return DockerTokenResponse(
        token=token,
        access_token=token,
        expires_in=int(ttl.total_seconds()),
        issued_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
