"""Docker registry token auth + per-department конфиг.

Документация протокола: https://distribution.github.io/distribution/spec/auth/token/

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

from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX, PlatformRole
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, NotFoundError
from src.core.security import hash_opaque_token
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
from src.services import _lockout, audit_service
from src.utils.time import is_expired, utcnow
from src.core.docker_jwt import sign_docker_token


def _cfg_to_response(cfg) -> DockerRegistryConfigResponse:
    """ORM DepartmentDockerRegistry → DTO."""
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
    """Создать или заменить Docker registry конфиг отдела."""
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
    """Patch конфига. Поля можно передавать частично."""
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
    """Текущий конфиг registry для отдела."""
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
    """Отключить registry для отдела (soft, `is_enabled=False`)."""
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
    """Распарсить Docker scope-строку `repository:foo/bar:pull,push` в структурный формат."""
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
    """Аутентификация через пароль / PAT / bot-токен. Возвращает (subject_id, department_id)."""
    user_repo = UserRepository(db)
    token_repo = TokenRepository(db)
    bot_token_repo = BotTokenRepository(db)
    bot_repo = BotRepository(db)

    if password.startswith(PAT_PREFIX):
        pat = await token_repo.get_active_by_hash(hash_opaque_token(password))
        if pat and not (pat.expires_at and is_expired(pat.expires_at)):
            user = await user_repo.get_by_id(pat.user_id)
            # `is_active` — boolean колонка, отражает result `status == ACTIVE`
            # (BANNED / BLOCKED / inactive выставляют False). Сравнение через
            # строку `user.status == "active"` ломается, если когда-то добавим
            # ещё один статус (`pending`/`archived`); is_active — единая точка.
            if user and user.is_active:
                # PAT валидный, но если у юзера активен password-lockout —
                # рубим и docker-auth: иначе атакующий, скомпрометировавший
                # PAT, обходит per-user 429 после 5 неуспешных password-логинов.
                # Сам факт lockout'а через Docker канал не раскрываем —
                # отвечаем generic `INVALID_CREDENTIALS`, чтобы держатель
                # PAT'а не получал side-channel «юзер сейчас залочен».
                try:
                    _lockout.assert_not_locked(user)
                except AuthorizationError:
                    raise AuthenticationError(
                        error_code="INVALID_CREDENTIALS", message="Invalid credentials"
                    )
                return user.id, user.department_id
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")

    if password.startswith(BOT_TOKEN_PREFIX):
        bot_token = await bot_token_repo.get_active_by_hash(hash_opaque_token(password))
        bot_for_success: object | None = None
        if bot_token and not (bot_token.expires_at and is_expired(bot_token.expires_at)):
            bot_for_success = await bot_repo.get_by_id(bot_token.bot_id)
            if bot_for_success and bot_for_success.is_active:
                # JIT-сброс истёкшего lockout'а перед happy-path.
                if await _lockout.release_principal_if_expired(
                    bot_repo, bot_for_success,
                    reset_attr="reset_failed_token_attempts",
                ):
                    await db.commit()
                _lockout.assert_principal_not_locked(bot_for_success)
                if bot_for_success.failed_token_attempts:
                    await bot_repo.reset_failed_token_attempts(bot_for_success)
                    await db.commit()
                return bot_for_success.id, bot_for_success.department_id

        # Token не валиден ИЛИ привязан к неактивному боту. Регистрируем
        # неуспех на боте, идентифицируя его по username (Basic-auth) —
        # bot.name не уникален, но первого совпадения достаточно для
        # счётчика. Симметрия с user-password путём.
        target_bot = await bot_repo.first_by_name(username) if username else None
        if target_bot is not None:
            if await _lockout.release_principal_if_expired(
                bot_repo, target_bot,
                reset_attr="reset_failed_token_attempts",
            ):
                await db.commit()
            _lockout.assert_principal_not_locked(target_bot)
            settings = get_settings()
            await _lockout.register_principal_failure(
                bot_repo,
                target_bot,
                counter_attr="failed_token_attempts",
                increment_method="increment_failed_token_attempts",
                max_attempts=settings.bot_max_failed_token_attempts,
                lockout_minutes=settings.bot_lockout_minutes,
            )
            await db.commit()
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")

    user = await user_repo.get_by_username(username)
    if user is None:
        # Не раскрываем существование юзера — та же ошибка, что и при wrong-pw.
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Invalid credentials")
    if user.status != "active":
        # Отбиваем BANNED / BLOCKED / inactive ДО Argon2id verify — экономим
        # CPU и держим ту же generic-ошибку, чтобы не палить состояние аккаунта
        # через Docker auth (зеркало `/login`, где USER_BANNED / USER_BLOCKED
        # уходит только в user-facing JSON-login, не в Docker Basic-auth канал).
        raise AuthenticationError(error_code="INVALID_CREDENTIALS", message="Account is not active")

    # ── Brute-force lockout pipeline ──────────────────────────────────────────
    # `/login` инкрементит `failed_login_attempts` и ставит `locked_until`
    # после 5 промахов; `/docker/token` исторически звал голый
    # `verify_password` и в lockout не участвовал. Atttacker'у со знанием
    # username хватило бы долбить `/docker/token` мимо per-user lockout
    # (Argon2id ~100ms был единственным тормозом).
    #
    # Общий helper `verify_password_with_lockout` (в `services/auth_service.py`)
    # гоняет тот же pipeline, что и `/login`: active-lockout → 429
    # ACCOUNT_TEMPORARILY_LOCKED ДО verify_password, failed verify → атомарный
    # инкремент + commit, на 5-й failure → lockout. Lazy import — чтобы
    # сохранить acyclic import-graph.
    from src.services.auth_service import verify_password_with_lockout
    await verify_password_with_lockout(db, user, password)
    return user.id, user.department_id


def _resolve_actions(cfg, subject_id: str, requested_actions: list[str]) -> list[str]:
    """Подмножество запрошенных actions, на которые у субъекта реально есть права."""
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
    """Выдать scoped Docker JWT (RS256, TTL ~5 мин) с access-claims по запрошенному scope."""
    settings = get_settings()
    try:
        subject_id, department_id = await _authenticate_subject(db, username, password)
    except AuthorizationError as exc:
        # Симметрия с `/login`: lockout-кейс должен оставлять audit-след,
        # иначе SOC слеп на brute-force через docker auth (на /login-канале
        # ACCOUNT_TEMPORARILY_LOCKED эмитится отдельно). Лезем в users по
        # username best-effort — он может быть PAT/bot-токеном, тогда
        # _authenticate_subject не доходит до этой ветки.
        if exc.error_code == "ACCOUNT_TEMPORARILY_LOCKED":
            actor_id = None
            try:
                user = await UserRepository(db).get_by_username(username)
                if user is not None:
                    actor_id = user.id
            except Exception:
                pass
            audit_service.emit(
                "docker.token_issued",
                actor_id,
                target_id=service or settings.docker_registry_service,
                target_type="docker_registry",
                status="failure",
                allowed=False,
                details={
                    "reason": "account_locked",
                    "username": username,
                    "service": service or settings.docker_registry_service,
                    "requested_scope": scope,
                    "retry_after_seconds": exc.details.get("retry_after_seconds"),
                },
                request_id=request_id,
            )
        raise
    except AuthenticationError as exc:
        # Зеркало lockout-ветки: brute-force через PAT/bot/пароль виден SOC
        # как поток `docker.token_issued failure`. Subject_type помечает
        # источник, чтобы отделить PAT-перебор от password-перебора.
        if password.startswith(PAT_PREFIX):
            subject_type = "pat"
        elif password.startswith(BOT_TOKEN_PREFIX):
            subject_type = "bot_token"
        else:
            subject_type = "password"
        actor_id = None
        if subject_type == "password":
            try:
                user = await UserRepository(db).get_by_username(username)
                if user is not None:
                    actor_id = user.id
            except Exception:
                pass
        audit_service.emit(
            "docker.token_issued",
            actor_id,
            target_id=service or settings.docker_registry_service,
            target_type="docker_registry",
            status="failure",
            allowed=False,
            details={
                "reason": "invalid_credentials",
                "subject_type": subject_type,
                "username": username,
                "service": service or settings.docker_registry_service,
                "requested_scope": scope,
                "error_code": exc.error_code,
            },
            request_id=request_id,
        )
        raise

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
