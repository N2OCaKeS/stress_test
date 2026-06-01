"""Зависимости аутентификации и авторизации.

`get_current_identity` revalidate'ит bearer JWT через БД на каждом запросе —
снимок из payload больше не даёт привилегий после ban/demote/role-revoke
до конца TTL. Поведение:

* Default `user` JWT (либо отсутствие `actor_type` — backward compat) —
  ищем юзера, отбиваем если нет/inactive/status≠ACTIVE, пересобираем
  privilege-поля из БД через `collect_user_permissions`.
* `actor_type="oauth_client"` — ищем OAuth client по `sub` (формат `cli_*`),
  отбиваем если нет/inactive; service-roles пустые (m2m токены без ролей).
* Прочие `actor_type` — reject. Здесь строже, чем в `introspect`: внутри
  auth_service поддерживаем только user + m2m.

Цена — 3-4 SQL на запрос (user + dept + roles + groups). Кэш — ниже.
"""

import hashlib
import logging
import secrets
import time
from collections import OrderedDict
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import KNOWN_SERVICE_IDENTITIES, PlatformRole, UserStatus
from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import decode_access_token
from src.dependencies.db import get_db
from src.repositories.departments import DepartmentRepository
from src.repositories.oauth_clients import OAuthClientRepository
from src.repositories.users import UserRepository
from src.schemas.auth import IdentityContext
from src.services.auth_service import collect_user_permissions

_service_bearer = HTTPBearer(auto_error=False)
_logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> str | None:
    """Вытащить Bearer-токен из `Authorization` header'а. None если нет."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _invalid_token_error() -> AuthenticationError:
    """Единая ошибка для невалидного/истёкшего JWT. Не палит конкретику."""
    return AuthenticationError(
        error_code="ACCESS_TOKEN_EXPIRED",
        message="Invalid or expired token",
    )


async def _identity_from_user_jwt(
    db: AsyncSession, payload: dict
) -> IdentityContext:
    """Revalidate user-actor JWT через БД.

    Stale payload больше не даёт привилегий: каждое privilege-поле
    (`platform_role`, `department_id`, `allowed_services`, `service_roles`)
    пересобирается из живой user-row + roles + groups.
    """
    sub = payload.get("sub")
    if not sub:
        raise _invalid_token_error()

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(sub)
    # `is_active=False` — soft-delete флаг; `status != ACTIVE` покрывает и
    # `banned`, и `blocked`. Любое из условий мгновенно убивает токен,
    # независимо от того, что в JWT payload.
    if user is None or not user.is_active or user.status != UserStatus.ACTIVE:
        raise AuthenticationError(
            error_code="USER_BANNED_OR_INACTIVE",
            message="User is no longer active",
        )

    # account_admin намеренно не имеет service-level grants (зеркало
    # `_build_identity` из services/auth_service.py).
    is_account_admin = user.platform_role == PlatformRole.ACCOUNT_ADMIN
    if is_account_admin:
        allowed_services: list[str] = []
        service_roles: dict[str, list[str]] = {}
        groups: dict[str, list[str]] = {}
    else:
        allowed_services, service_roles, groups = await collect_user_permissions(db, user)

    dept_name: str | None = None
    if user.department_id:
        dept = await DepartmentRepository(db).get_by_id(user.department_id)
        dept_name = dept.display_name if dept else None

    return IdentityContext(
        user_id=user.id,
        username=user.username,
        department_id=user.department_id,
        department_name=dept_name,
        allowed_services=allowed_services,
        service_roles=service_roles,
        groups=groups,
        is_banned=user.status == UserStatus.BANNED,
        platform_role=user.platform_role,
        subject_type="user",
    )


async def _identity_from_oauth_client_jwt(
    db: AsyncSession, payload: dict
) -> IdentityContext:
    """Revalidate OAuth2 `client_credentials` JWT через БД.

    `sub` здесь — `client.client_id` (`cli_*`), не user_id, идём в
    OAuthClientRepository. Зеркало `introspect`'а: чтобы revoke
    (DELETE /oauth2/clients/{id} → `is_active=False`) действовал мгновенно.
    """
    sub = payload.get("sub")
    if not sub:
        raise _invalid_token_error()

    client = await OAuthClientRepository(db).get_by_client_id(sub)
    if client is None or not client.is_active:
        raise AuthenticationError(
            error_code="USER_BANNED_OR_INACTIVE",
            message="OAuth client is no longer active",
        )

    # Пересчитываем allowed_services как live (dept-grants ∩ client.scopes),
    # чтобы revoke на уровне отдела моментально доходил до guard'а.
    dept_repo = DepartmentRepository(db)
    dept_services = (
        await dept_repo.list_active_services(client.department_id)
        if client.department_id
        else []
    )
    allowed_services = [s for s in dept_services if s in client.allowed_scopes]

    # OAuth client_credentials — m2m, никаких platform_role или per-service
    # ролей: пустые, чтобы `require_*_admin` их отбивал. `subject_type=
    # "oauth_client"` нужен `require_user_context`-guard'у для отсева m2m
    # на user-facing endpoint'ах (иначе FK violation / audit-injection
    # через /oauth2/authorize и т.д.).
    return IdentityContext(
        user_id=client.client_id,
        username=client.name,
        department_id=client.department_id,
        allowed_services=allowed_services,
        service_roles={},
        is_banned=False,
        platform_role=None,
        subject_type="oauth_client",
    )


async def get_current_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IdentityContext:
    """Достать текущий identity из Bearer JWT.

    Декодит JWT (подпись + expiry), потом revalidate'ит субъекта через БД и
    пересобирает privilege-поля — stale JWT не удерживает забаненного/
    разжалованного actor'а до конца TTL.

    Сверху TTL-кэш ~5s по `token_hash`, чтобы rapid same-token requests не
    били БД 3-4 SQL'ями каждый. Invalidation двойная: активная (хук из
    ban/role-change) и пассивная (TTL). Ban после хука = моментально,
    без — задержка до TTL.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_EXPIRED",
            message="Missing bearer token",
        )

    # ── TTL-кэш ────────────────────────────────────────────────────────────
    cached = _identity_cache_get(token)
    if cached is not None:
        _propagate_identity_to_audit_context(cached)
        return cached

    # Middleware (`_extract_actor_info`) уже декодит JWT для audit-контекста
    # и кладёт payload в request.state.jwt_payload — переиспользуем, чтобы
    # не платить HS256-decode второй раз за запрос.
    payload = getattr(request.state, "jwt_payload", None)
    if payload is None:
        try:
            payload = decode_access_token(token)
        except Exception:
            raise _invalid_token_error()

    # `actor_type` — диспатч между user-JWT и oauth_client_credentials.
    # Отсутствие → "user" для backward compat со старыми JWT.
    actor_type = payload.get("actor_type") or "user"

    if actor_type == "user":
        identity = await _identity_from_user_jwt(db, payload)
    elif actor_type == "oauth_client":
        identity = await _identity_from_oauth_client_jwt(db, payload)
    else:
        # Unknown actor_type — не доверяем JWT неизвестных типов (например
        # будущий "bot" actor_type потребует своей ветки revalidate'а).
        raise _invalid_token_error()

    _identity_cache_put(token, identity)
    _propagate_identity_to_audit_context(identity)
    return identity


# ── TTL-cache для identity ───────────────────────────────────────────────────
# Module-level OrderedDict — один процесс на pod, GIL даёт thread-safety для
# атомарных dict-операций. Key = sha256(token) — heap-dump-safe. Value =
# (IdentityContext, expires_at). TTL=5s — окно burst'а одной UI-сессии, но
# недостаточно для долгого stale-доступа забаненного.
#
# Размер ограничен `_IDENTITY_CACHE_MAXSIZE`: при превышении вытесняем самый
# старый по вставке (LRU-ish — мы re-insert'им запись при cache hit, см.
# `_identity_cache_get`). Без cap'а burst коротко-живущих токенов (PAT/bot
# per-request) гнал бы кэш в неограниченный рост → OOM pod'а.

# TTL=5s по умолчанию; читается из Settings (env `IDENTITY_CACHE_TTL_SECONDS`).
# 0 / отрицательное — disable (тесты, мутирующие User прямым SQL'ом мимо
# invalidate-хуков). Конфиг достаётся через `_get_cache_config()` лениво на
# каждый cache-hit. Модульные имена `_IDENTITY_CACHE_TTL_SECONDS` /
# `_IDENTITY_CACHE_MAXSIZE` сохранены как legacy-точки monkeypatch'а тестов —
# если они выставлены явно (не None), они перекрывают settings.
_IDENTITY_CACHE_TTL_SECONDS: float | None = None
_IDENTITY_CACHE_MAXSIZE: int | None = None
_identity_cache: "OrderedDict[str, tuple[IdentityContext, float]]" = OrderedDict()
# Обратный индекс user_id → {cache_key}. Поддерживается в `_identity_cache_put`
# / `_identity_cache_get` (lazy GC) / `popitem`-eviction. Делает
# `invalidate_identity_cache_for_user` O(K), где K — число entries конкретного
# юзера, вместо O(N) скана всего кэша. При default maxsize=50k и burst'е PAT/
# bot-токенов линейный скан становился заметным.
_user_index: dict[str, set[str]] = {}


def _get_cache_config() -> tuple[float, int]:
    """Текущие TTL и maxsize кэша. Lazy чтение из Settings — тестовые
    env-override подхватываются после `get_settings.cache_clear()`. Module-level
    overrides (`_IDENTITY_CACHE_TTL_SECONDS`/`_MAXSIZE`) держат приоритет ради
    тестов, которые monkeypatch'ат их напрямую.
    """
    if _IDENTITY_CACHE_TTL_SECONDS is not None and _IDENTITY_CACHE_MAXSIZE is not None:
        return float(_IDENTITY_CACHE_TTL_SECONDS), int(_IDENTITY_CACHE_MAXSIZE)
    settings = get_settings()
    ttl = (
        float(_IDENTITY_CACHE_TTL_SECONDS)
        if _IDENTITY_CACHE_TTL_SECONDS is not None
        else float(settings.identity_cache_ttl_seconds)
    )
    maxsize = (
        int(_IDENTITY_CACHE_MAXSIZE)
        if _IDENTITY_CACHE_MAXSIZE is not None
        else int(settings.identity_cache_maxsize)
    )
    return ttl, maxsize


def _token_cache_key(token: str) -> str:
    """SHA-256 от raw-токена — key в TTL-кэше.

    Не сам токен в plaintext: (а) heap-dump-safety; (б) короче в логах,
    если кэш будут когда-то дампить для observability. SHA-256 collision-
    free на любых разумных размерах кэша.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _drop_from_user_index(user_id: str, key: str) -> None:
    """Снять `key` с обратного индекса user_id; чистит пустые set'ы."""
    keys = _user_index.get(user_id)
    if keys is None:
        return
    keys.discard(key)
    if not keys:
        _user_index.pop(user_id, None)


def _identity_cache_get(token: str) -> IdentityContext | None:
    """Достать identity из кэша. None если miss/expired (с lazy GC).

    TTL<=0 → кэш disabled (тестовый режим), всегда miss.
    """
    ttl, _ = _get_cache_config()
    if ttl <= 0:
        return None
    key = _token_cache_key(token)
    entry = _identity_cache.get(key)
    if entry is None:
        return None
    identity, expires_at = entry
    if time.time() >= expires_at:
        # Lazy GC — удаляем при miss-after-expire, чтобы dict не рос
        # бесконечно для коротко-живущих токенов.
        _identity_cache.pop(key, None)
        _drop_from_user_index(identity.user_id, key)
        return None
    # Cache hit — двигаем запись в конец, чтобы eviction выбрасывал реально
    # давно не используемые токены, а не недавно прочитанные.
    _identity_cache.move_to_end(key)
    return identity


def _identity_cache_put(token: str, identity: IdentityContext) -> None:
    """Положить identity в кэш с expires_at = now + TTL.

    TTL<=0 → no-op (кэш disabled). При переполнении maxsize
    вытесняем самые старые записи.
    """
    ttl, maxsize = _get_cache_config()
    if ttl <= 0:
        return
    key = _token_cache_key(token)
    # Если key уже был под другим user_id (теоретически невозможно при
    # sha256 от raw-токена, но защищаемся от reseed/тестового мусора) —
    # снимем старую запись из обратного индекса.
    prev = _identity_cache.get(key)
    if prev is not None and prev[0].user_id != identity.user_id:
        _drop_from_user_index(prev[0].user_id, key)
    _identity_cache[key] = (identity, time.time() + ttl)
    _identity_cache.move_to_end(key)
    _user_index.setdefault(identity.user_id, set()).add(key)
    while len(_identity_cache) > maxsize:
        evicted_key, (evicted_identity, _) = _identity_cache.popitem(last=False)
        _drop_from_user_index(evicted_identity.user_id, evicted_key)


def _identity_cache_clear() -> None:
    """Полная очистка кэша. Используется в тестах (фикстура `client` создаёт
    новый app, но module-level state переживает между тестами в той же сессии).
    """
    _identity_cache.clear()
    _user_index.clear()


def invalidate_identity_cache_for_user(user_id: str) -> int:
    """Сбросить все cached identity юзера.

    Дёргают `ban_user`/`unban_user`/`update_user` при смене
    platform_role/department/status — privilege-change должен сработать
    моментально, не ждать TTL. Возвращает число удалённых entries (для
    тестов/observability). Через обратный индекс `_user_index` — O(K) по
    числу записей юзера, не O(N) по всему кэшу.
    """
    keys = _user_index.pop(user_id, None)
    if not keys:
        return 0
    for key in keys:
        _identity_cache.pop(key, None)
    return len(keys)


def _propagate_identity_to_audit_context(identity: IdentityContext) -> None:
    """После get_current_identity-revalidate обновляем audit_context.

    Middleware заполнил `subject_type` из JWT claim'а, но (а) у legacy JWT
    claim мог отсутствовать (None или "user"-fallback), (б) актор мог быть
    разжалован между login'ом и текущим запросом — `IdentityContext` после
    revalidate'а отражает реальность БД. Пишем тот же `subject_type`, чтобы
    последующие emit'ы во время этого request'а взяли свежее значение.
    """
    # Lazy import — `src.services.audit_context` тянется до get_current_identity,
    # но top-level импорт создал бы цикл (audit_context → settings → ничего, но
    # dependencies/auth → services → audit_context — нагрузим через runtime).
    from src.services import audit_context as _audit_context_mod

    _audit_context_mod.update_context(
        subject_type=identity.subject_type,
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
    )


CurrentIdentity = Annotated[IdentityContext, Depends(get_current_identity)]


def require_user_context(identity: CurrentIdentity) -> IdentityContext:
    """Отбивает m2m identity (OAuth `client_credentials`) на user-facing endpoint'ах.

    После того как `get_current_identity` начал диспатчить и пропускать
    `actor_type="oauth_client"`, m2m-JWT мог попасть на /me, /users/*,
    /tokens и т.д. — `identity.user_id="cli_*"` улетал в ORM-операции,
    audit-trail и FK-колонки на `users.id`. Здесь режем с 403
    `USER_CONTEXT_REQUIRED`.

    Service-to-service endpoint'ы (`/authorization/introspect`,
    `/authorization/service-access`) живут на `require_service_token` и
    этот guard не используют.
    """
    if identity.subject_type != "user":
        raise AuthorizationError(
            error_code="USER_CONTEXT_REQUIRED",
            message="This endpoint requires user context; m2m tokens are not accepted",
        )
    return identity


CurrentUserIdentity = Annotated[IdentityContext, Depends(require_user_context)]


def require_account_admin(identity: CurrentUserIdentity) -> IdentityContext:
    """Гард — требует platform_role=account_admin. Иначе 403 ROLE_REQUIRED."""
    if identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        raise AuthorizationError(
            error_code="ROLE_REQUIRED",
            message="account_admin role required",
        )
    return identity


def require_any_admin(identity: CurrentUserIdentity) -> IdentityContext:
    """Гард — пускает и account_admin, и department_admin."""
    if identity.platform_role not in (PlatformRole.ACCOUNT_ADMIN, PlatformRole.DEPARTMENT_ADMIN):
        raise AuthorizationError(
            error_code="ROLE_REQUIRED",
            message="Admin role required",
        )
    return identity


AccountAdmin = Annotated[IdentityContext, Depends(require_account_admin)]
AnyAdmin = Annotated[IdentityContext, Depends(require_any_admin)]


def require_service_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_service_bearer),
) -> None:
    """Гард для service-to-service эндпоинтов.

    Dual-mode auth:

    * `SERVICE_API_KEYS` (JSON dict в env) непустой → lookup по
      `X-Service-Identity`. Header обязателен, Bearer сравнивается с
      `SERVICE_API_KEYS[identity]` через `compare_digest`.
    * Иначе legacy — один shared `SERVICE_API_KEY`. Header опционален
      (см. soft/strict-режим в `STRICT_SERVICE_IDENTITY`).

    Legacy-режим: header нет — backward-compat; есть и в
    `KNOWN_SERVICE_IDENTITIES` — стэшим в `request.state`; не в allow-list —
    WARNING (soft) или 401 в strict.
    """
    settings = get_settings()
    if credentials is None:
        raise AuthenticationError(
            error_code="INVALID_SERVICE_TOKEN",
            message="Valid service API key required",
        )

    raw_service_identity = request.headers.get("X-Service-Identity")
    service_identity = (
        raw_service_identity.strip() if raw_service_identity is not None else None
    )
    if service_identity == "":
        service_identity = "<empty>"

    # ── Per-service keys (dual-mode) ─────────────────────────────────────────
    # Если задан непустой `SERVICE_API_KEYS` — это authoritative source.
    # Header обязателен, без него невозможно выбрать ключ для сравнения.
    if settings.service_api_keys:
        if service_identity is None or service_identity == "<empty>":
            raise AuthenticationError(
                error_code="MISSING_SERVICE_IDENTITY",
                message="X-Service-Identity header required when SERVICE_API_KEYS is configured",
            )
        expected_key = settings.service_api_keys.get(service_identity)
        if expected_key is None or not secrets.compare_digest(
            credentials.credentials, expected_key
        ):
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Valid service API key required",
            )
        request.state.service_identity = service_identity
        return

    # ── Legacy shared secret ─────────────────────────────────────────────────
    # Если `SERVICE_API_KEYS` непустой, мы уже вернулись выше — сюда попадаем
    # только при пустом per-service словаре. Но если `strict_service_api_keys`
    # включён И словарь непустой — это означает «legacy fallback запрещён»;
    # в норме сюда не попадём, однако защищаемся явным reject'ом на случай
    # будущих рефакторингов ветки выше.
    if settings.service_api_keys and settings.strict_service_api_keys:
        raise AuthenticationError(
            error_code="INVALID_SERVICE_TOKEN",
            message="Legacy SERVICE_API_KEY fallback disabled (STRICT_SERVICE_API_KEYS=true)",
        )
    if not secrets.compare_digest(credentials.credentials, settings.service_api_key):
        raise AuthenticationError(
            error_code="INVALID_SERVICE_TOKEN",
            message="Valid service API key required",
        )

    if service_identity is not None:
        if service_identity in KNOWN_SERVICE_IDENTITIES:
            request.state.service_identity = service_identity
        else:
            _logger.warning(
                "auth: unknown X-Service-Identity header value=%r path=%s; "
                "valid SERVICE_API_KEY present, request %s",
                service_identity,
                request.url.path,
                "REJECTED (STRICT_SERVICE_IDENTITY=true)"
                if settings.strict_service_identity
                else "allowed (soft mode)",
            )
            if settings.strict_service_identity:
                raise AuthenticationError(
                    error_code="INVALID_SERVICE_IDENTITY",
                    message=(
                        "X-Service-Identity header does not match any known "
                        "service in the allow-list"
                    ),
                )
