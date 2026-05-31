"""Workflow авторизации: introspect токенов + проверка service-access.

Используется service-to-service (вызывают config_service / loging_service /
server_service). Чувствительные claims (is_banned, allowed_services,
service_roles) всегда revalidate'ятся из БД, не берутся из JWT payload.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole, SubjectType, UserStatus
from src.core.security import decode_access_token, hash_opaque_token
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.oauth_clients import OAuthClientRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.authorization import IntrospectResponse, ServiceAccessResponse
from src.services import audit_service
from src.services.auth_service import collect_bot_permissions, collect_user_permissions
from src.utils.time import is_expired


async def _introspect_oauth_client_jwt(
    db: AsyncSession,
    sub: str | None,
    payload: dict,
    request_id: str | None,
) -> IntrospectResponse:
    """Revalidate OAuth2 `client_credentials` JWT через БД.

    У таких JWT `sub == client.client_id` (`cli_*`), поэтому UserRepository
    не подходит. Ищем клиента, проверяем что активен, пересобираем
    `allowed_services` как INTERSECT dept-services с allowed_scopes клиента —
    зеркалит логику `client_credentials_token` на issue-time, чтобы revoke
    действовал сразу.
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(sub) if sub else None
    if client is None or not client.is_active:
        audit_service.emit(
            "token.introspect",
            sub,
            actor_type="oauth_client",
            status="failure",
            allowed=False,
            details={
                "token_type": "jwt",
                "reason": (
                    "oauth_client_not_found" if client is None else "oauth_client_inactive"
                ),
                "client_id": sub,
                "exp": payload.get("exp"),
            },
            request_id=request_id,
        )
        return IntrospectResponse(active=False)

    dept_repo = DepartmentRepository(db)
    # Если у клиента не выставлен department_id — пропускаем SELECT с
    # `WHERE department_id IS NULL` (он гарантированно вернёт []). Симметрично
    # с `dependencies/auth._identity_from_oauth_client_jwt`, где такой же guard
    # уже стоит на cold-пути.
    if client.department_id:
        dept_services = await dept_repo.list_active_services(client.department_id)
        allowed_services = [s for s in dept_services if s in client.allowed_scopes]
        dept = await dept_repo.get_by_id(client.department_id)
    else:
        allowed_services = []
        dept = None

    audit_service.emit(
        "token.introspect",
        client.id,
        actor_type="oauth_client",
        department_id=client.department_id,
        target_id=client.client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={
            "token_type": "jwt",
            "client_id": client.client_id,
            "client_name": client.name,
            "allowed_services": allowed_services,
            "exp": payload.get("exp"),
        },
        request_id=request_id,
    )
    return IntrospectResponse(
        active=True,
        subject_type=SubjectType.OAUTH_CLIENT,
        sub=client.client_id,
        username=client.name,
        department_id=client.department_id,
        department_name=dept.display_name if dept else None,
        platform_role=None,
        is_banned=False,
        allowed_services=allowed_services,
        service_roles={},
        exp=payload.get("exp"),
    )


async def introspect(
    db: AsyncSession,
    token: str,
    request_id: str | None = None,
    caller_ip: str | None = None,
) -> IntrospectResponse:
    """Валидирует любой тип токена (JWT / PAT / bot) и возвращает контекст субъекта.

    `caller_ip` — IP конечного клиента, проброшенный вызывающим сервисом.
    Используется только в bot-ветке для детектора `bot.suspicious_multi_ip`.
    """

    # 1. Пробуем JWT access token.
    #
    # SECURITY: JWT короткоживущий, но его payload (`platform_role`,
    # `allowed_services`, `service_roles`, ban state) — снимок на момент login.
    # Между login и истечением аккаунт могли забанить, отозвать service access
    # на уровне отдела, снять роль или удалить юзера — JWT НЕ должен сохранять
    # старые привилегии. Поэтому декодим JWT только для проверки подписи/exp,
    # потом re-fetch'аем субъекта из БД и пересобираем все privilege-поля
    # заново. Зеркально с PAT/bot ветками ниже.
    try:
        payload = decode_access_token(token)
    except Exception:
        payload = None

    if payload is not None:
        sub = payload.get("sub")
        # `actor_type` зашит в JWT payload на issue-time и указывает, как
        # revalidate'ить субъекта из БД:
        #   * "user" (или отсутствие → backward compat для старых JWT) →
        #     revalidate через UserRepository.
        #   * "oauth_client" → revalidate через OAuthClientRepository — JWT
        #     выписан через OAuth2 `client_credentials` grant, `sub` это
        #     `client.client_id` (`cli_*`), НЕ user_id, UserRepository всегда
        #     промахнётся.
        actor_type = payload.get("actor_type") or "user"

        if actor_type == "oauth_client":
            return await _introspect_oauth_client_jwt(db, sub, payload, request_id)

        # Дефолтная ветка: user JWT.
        user_repo = UserRepository(db)
        dept_repo = DepartmentRepository(db)
        user = await user_repo.get_by_id(sub) if sub else None

        # Юзера нет / деактивирован / забанен → токен мёртв, что бы payload
        # ни декларировал.
        if user is None or not user.is_active or user.status != UserStatus.ACTIVE:
            audit_service.emit(
                "token.introspect",
                sub,
                actor_type="user",
                status="failure",
                allowed=False,
                details={
                    "token_type": "jwt",
                    "reason": (
                        "user_not_found"
                        if user is None
                        else "banned" if user.status == UserStatus.BANNED
                        else "blocked" if user.status == UserStatus.BLOCKED
                        else "inactive"
                    ),
                    # `payload.get("username")` всегда был None — username не
                    # лежит в JWT (только `sub + actor_type`). Берём из БД,
                    # если юзер вообще нашёлся; для user_not_found поля нет.
                    "username": user.username if user is not None else None,
                    "exp": payload.get("exp"),
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)

        # Permissions: пересобираем live из БД, не из payload. Для
        # account_admin не отдаём service-grants (зеркало `_build_identity`).
        #
        # OAuth scope-creep guard: если JWT был выписан через
        # `authorization_code` grant, в payload лежит `oauth_scopes` —
        # снапшот тех scope'ов, что юзер аппрувнул при /authorize. Без этой
        # фильтрации third-party app со scope=["svc_a"] видел бы через
        # introspect все live-сервисы юзера ({svc_a, svc_b, svc_c}). Поле
        # `None` означает «не OAuth-токен» — фильтрация не нужна; пустой
        # список = «вообще ничего».
        oauth_scopes = payload.get("oauth_scopes")
        # Дополнительно пересекаем с live `client.allowed_scopes`: если
        # администратор сузил список scope'ов клиента после выпуска кода,
        # старые JWT не должны видеть отозванные сервисы. Клиента ищем по
        # `oauth_client_id` из payload; если клиент уже снесён / деактивирован
        # — обнуляем scopes (токен фактически мёртв).
        if oauth_scopes is not None:
            oauth_cid = payload.get("oauth_client_id")
            if oauth_cid:
                _client_repo = OAuthClientRepository(db)
                _client = await _client_repo.get_by_client_id(oauth_cid)
                if _client is None or not _client.is_active:
                    oauth_scopes = []
                else:
                    _client_allowed = set(_client.allowed_scopes or [])
                    oauth_scopes = [s for s in oauth_scopes if s in _client_allowed]

        is_account_admin = user.platform_role == PlatformRole.ACCOUNT_ADMIN
        if is_account_admin:
            allowed_services: list[str] = []
            service_roles: dict[str, list[str]] = {}
            groups: dict[str, list[str]] = {}
        else:
            allowed_services, service_roles, groups = await collect_user_permissions(
                db, user, oauth_scopes=oauth_scopes
            )

        dept = await dept_repo.get_by_id(user.department_id) if user.department_id else None

        audit_details: dict = {
            "token_type": "jwt",
            "username": user.username,
            "platform_role": user.platform_role,
            "allowed_services": allowed_services,
            "exp": payload.get("exp"),
        }
        # Для OAuth-JWT фиксируем `oauth_client_id` + `oauth_scopes` — это
        # позволяет в логах увидеть, что introspect был обрезан по scope
        # выписанного auth_code, а не по полным правам юзера.
        if oauth_scopes is not None:
            audit_details["oauth_scopes"] = list(oauth_scopes)
            oauth_cid = payload.get("oauth_client_id")
            if oauth_cid:
                audit_details["oauth_client_id"] = oauth_cid
        audit_service.emit(
            "token.introspect",
            user.id,
            actor_type="user",
            department_id=user.department_id,
            status="success",
            allowed=True,
            details=audit_details,
            request_id=request_id,
        )
        return IntrospectResponse(
            active=True,
            subject_type=SubjectType.USER,
            sub=user.id,
            username=user.username,
            department_id=user.department_id,
            department_name=dept.display_name if dept else None,
            platform_role=user.platform_role,
            is_banned=user.status == UserStatus.BANNED,
            allowed_services=allowed_services,
            service_roles=service_roles,
            groups=groups,
            exp=payload.get("exp"),
        )

    # 2. Пробуем PAT
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
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(pat.user_id)
        if user is None:
            audit_service.emit(
                "token.introspect", pat.user_id, status="failure", allowed=False,
                details={
                    "token_type": "pat",
                    "reason": "user_not_found",
                    "pat_id": pat.id,
                    "token_prefix": pat.token_prefix,
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
        # ── Симметрия с JWT-introspect ──────────────────────────────────────
        # JWT-introspect revalidate'ит: `is_active=False` или
        # `status != ACTIVE` → active=False. PAT-introspect должен возвращать
        # ровно то же — иначе caller'ы (server_service, config_service)
        # увидят разное поведение для двух типов токенов одного юзера.
        # Ban сейчас revoke'ит PAT через `token_repo.revoke_all_for_user` —
        # `get_active_by_hash` отфильтрует. Но если PAT уцелел (race, легаси
        # миграция, тест без полного `ban_user`) — режем здесь. Defence in
        # depth.
        if not user.is_active or user.status != UserStatus.ACTIVE:
            audit_service.emit(
                "token.introspect", user.id, status="failure", allowed=False,
                details={
                    "token_type": "pat",
                    "reason": (
                        "banned" if user.status == UserStatus.BANNED
                        else "blocked" if user.status == UserStatus.BLOCKED
                        else "inactive"
                    ),
                    "username": user.username,
                    "pat_id": pat.id,
                    "token_prefix": pat.token_prefix,
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
        # last_used_at дёргаем только после revalidate'а юзера: для banned/blocked
        # introspect отвечает active=False, "касаться" такой PAT смысла нет —
        # лишний UPDATE и недостоверная статистика "недавно использован".
        await token_repo.touch(pat)
        # Effective view юзера revalidate'им из БД через тот же путь, что и
        # JWT-ветка: collect_user_permissions учитывает dept-access И
        # group-derived service-access (group_services + group_roles). Без этого
        # PAT не видел бы сервисы, доступные юзеру только через группу —
        # асимметрия с JWT того же юзера. PAT по-прежнему ограничен своим
        # allowed_services (subset прав юзера, не расширение).
        allowed_services_full, service_roles_full, groups_full = await collect_user_permissions(db, user)
        allowed_set = set(allowed_services_full)
        # Защитный `or []` — у PAT.allowed_services стоит NOT NULL, но при
        # возможной миграции назад или legacy-row держим инвариант (bot-ветка
        # уже защищена тем же приёмом).
        effective_services = [s for s in (pat.allowed_services or []) if s in allowed_set]
        effective_roles = {
            k: v for k, v in service_roles_full.items() if k in effective_services
        }
        # PAT-ветка: groups режем по effective_services (PAT'у позволено только
        # подмножество прав юзера); пустые после фильтрации группы выкидываем.
        effective_groups: dict[str, list[str]] = {}
        for gname, items in groups_full.items():
            kept = [i for i in items if i.split(".", 1)[0] in effective_services]
            if kept:
                effective_groups[gname] = kept
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
            username=user.username,
            department_id=user.department_id,
            platform_role=user.platform_role,
            # `is_banned` симметрично JWT-ветке: `status == BANNED`. Раньше
            # тут было `not user.is_active`, что для BLOCKED-юзера ставило
            # `is_banned=True` (хотя BLOCKED это не ban). Но мы сюда не
            # доходим при BLOCKED (active=False выше); это покрытие на случай
            # будущих переходов состояний.
            is_banned=user.status == UserStatus.BANNED,
            allowed_services=effective_services,
            service_roles=effective_roles,
            groups=effective_groups,
        )

    # 3. Пробуем bot-токен
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
            # Парный CRITICAL-сигнал для SIEM: легитимный путь — dept_admin
            # должен перевыпустить токен; одиночное событие безобидно, но
            # серия `bot.token_expired` от одного бота означает, что
            # consumer не следит за TTL.
            audit_service.emit(
                "bot.token_expired",
                bot_token.bot_id,
                actor_type="bot",
                target_id=bot_token.id,
                target_type="bot_token",
                status="failure",
                allowed=False,
                details={
                    "bot_id": bot_token.bot_id,
                    "bot_token_id": bot_token.id,
                    "token_name": bot_token.name,
                    "token_prefix": bot_token.token_prefix,
                    "expires_at": bot_token.expires_at.isoformat(),
                    "error_code": "BOT_TOKEN_EXPIRED",
                },
                request_id=request_id,
            )
            return IntrospectResponse(active=False)
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
        # touch только после revalidate'а бота: для inactive bot введён ровно
        # тот же приём, что и в PAT-ветке для banned/blocked юзера — лишний
        # UPDATE для токена, который мы всё равно отклонили, искажает
        # «недавно использован» в админке.
        await bot_token_repo.touch(bot_token)
        effective_services, effective_roles = await collect_bot_permissions(db, bot)

        # Multi-IP detector: пишем caller_ip в `bot.last_known_ips`, при
        # необходимости — эмитим CRITICAL audit. Lazy-import чтобы не тянуть
        # модуль на cold start, когда bot-токенов в introspect ещё не было.
        from src.services.bot_ip_tracker import track_bot_ip
        await track_bot_ip(db, bot, caller_ip, request_id=request_id)

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
            service_roles=effective_roles,
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
    """Проверка «есть ли у субъекта access к конкретному сервису».

    Под капотом — full introspect + фильтр по `service_name`. Для проверки
    одного сервиса введён отдельный эндпоинт, чтобы server_service мог
    отдельно ходить за access-чеком без full identity-нагрузки.
    """
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
            status="failure",
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
            status="failure",
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
