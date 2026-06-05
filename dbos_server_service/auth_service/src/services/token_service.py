"""Personal Access Tokens (PAT): создание, листинг, revoke."""

from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.core.security import generate_pat
from src.repositories.departments import DepartmentRepository
from src.repositories.tokens import TokenRepository
from src.repositories.users import UserRepository
from src.schemas.tokens import PATCreateResponse, PATListItem
from src.services import audit_service
from src.utils.time import utcnow


async def create_pat(
    db: AsyncSession,
    actor_id: str,
    name: str,
    allowed_services: list[str],
    expires_at=None,
    request_id: str | None = None,
) -> PATCreateResponse:
    """Создать PAT. Raw возвращается один раз — больше нигде не показываем.

    Валидации:
    * `allowed_services ⊆ dept.allowed_services` — нельзя выдать PAT scope-нутый
      на сервис, к которому отдел юзера не имеет access. Иначе юзер мог бы
      минтить токены под чужие сервисы и подгонять под cross-tenant scope.
    * `expires_at > now()` — мёртвый токен с ttl в прошлом мусорит БД.
      account_admin без отдела — пропускаем dept-чек (у них нет dept).

    Уникальность имени держим только в рамках **активных** PAT юзера: после
    revoke имя свободно для пересоздания — это штатный flow ротации.
    """
    token_repo = TokenRepository(db)
    dept_repo = DepartmentRepository(db)
    user_repo = UserRepository(db)

    exp_dt = None
    if expires_at is not None:
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

    if allowed_services:
        actor = await user_repo.get_by_id(actor_id)
        # Fail-closed: JWT валидный, actor-row уже удалён (race delete →
        # token issue) — иначе validation скипалась бы по `actor is None`
        # и юзер минтил бы PAT под любые сервисы. Зеркало `ACTOR_VANISHED`
        # в `_dept_guard.assert_dept_admin_target_dept`.
        if actor is None:
            raise AuthorizationError(
                error_code="ACTOR_VANISHED",
                message="Actor no longer exists",
            )
        # actor.department_id is None — два кейса:
        #   * platform admin (account_admin / loging_admin / loging_reader) —
        #     by-design без отдела, scope глобальный, скип валидации.
        #   * обычный юзер без отдела (legacy/seed-edge) — отдела нет,
        #     значит и `dept.services` нет; считаем глобальный scope.
        # Оба кейса трактуем одинаково, без раздельной ветки.
        if actor.department_id:
            dept_services_list = await dept_repo.list_active_services(actor.department_id)
            dept_services = set(dept_services_list)
            forbidden = sorted(set(allowed_services) - dept_services)
            if forbidden:
                raise DomainValidationError(
                    error_code="SERVICE_NOT_ALLOWED_FOR_DEPARTMENT",
                    message="PAT cannot scope to services the department has no access to",
                    details={
                        "forbidden_services": forbidden,
                        # Подсказка юзеру, из чего можно выбрать. Пустой
                        # список означает, что отдел вообще не имеет ни
                        # одного активного сервиса.
                        "available_services": sorted(dept_services_list),
                    },
                )

    if await token_repo.exists_name(actor_id, name):
        raise ConflictError(error_code="TOKEN_NAME_ALREADY_EXISTS", message=f"Token '{name}' already exists")

    raw, prefix, token_hash = generate_pat()
    # `exists_name`-чек выше может проиграть гонку: два параллельных
    # запроса с одинаковым `name` оба видят 0 строк, оба создают PAT,
    # и второй коммит уносит IntegrityError на `uq_pat_user_name_active`.
    # Без перехвата ORM-исключение поднимается до глобального handler'а
    # как 500. Сворачиваем в стабильный 409 — тот же error_code, что и
    # на честно прошедшем `exists_name`-чеке.
    try:
        pat = await token_repo.create(
            user_id=actor_id,
            name=name,
            token_hash=token_hash,
            token_prefix=prefix,
            allowed_services=allowed_services,
            expires_at=exp_dt,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            error_code="TOKEN_NAME_ALREADY_EXISTS",
            message=f"Token '{name}' already exists",
        )
    # raw PAT уходит в details — sanitizer заменит на <TOKEN> по эвристике dbos_pat_…
    audit_service.emit(
        "pat.create", actor_id, target_id=pat.id, target_type="pat",
        request_id=request_id,
        details={
            "name": name,
            "token_prefix": prefix,
            "token": raw,
            "allowed_services": list(allowed_services),
            "expires_at": exp_dt.isoformat() if exp_dt else None,
        },
    )
    return PATCreateResponse(token_id=pat.id, token=raw, name=pat.name, expires_at=pat.expires_at)


async def list_pats(db: AsyncSession, actor_id: str, request_id: str | None = None) -> list[PATListItem]:
    """Свои PAT — только метаданные."""
    token_repo = TokenRepository(db)
    result = [
        PATListItem(
            token_id=p.id,
            name=p.name,
            token_prefix=p.token_prefix,
            allowed_services=p.allowed_services,
            created_at=p.created_at,
            expires_at=p.expires_at,
            last_used_at=p.last_used_at,
            revoked_at=p.revoked_at,
        )
        for p in await token_repo.list_for_user(actor_id)
    ]
    audit_service.emit(
        "pat.list", actor_id, status="success", allowed=True, request_id=request_id,
        details={"count": len(result)},
    )
    return result


async def revoke_pat(
    db: AsyncSession,
    actor_id: str,
    token_id: str,
    request_id: str | None = None,
) -> None:
    """Отозвать свой PAT. Чужие — `TOKEN_NOT_FOUND` (а не 403, чтобы не было oracle)."""
    token_repo = TokenRepository(db)
    pat = await token_repo.get_by_id(token_id)

    if pat is None or pat.user_id != actor_id:
        raise NotFoundError(error_code="TOKEN_NOT_FOUND", message="Token not found")

    if pat.revoked_at is not None:
        raise ConflictError(error_code="TOKEN_ALREADY_REVOKED", message="Token is already revoked", details={"token_id": token_id})

    await token_repo.revoke(pat)
    await db.commit()
    audit_service.emit(
        "pat.revoke", actor_id, target_id=token_id, target_type="pat",
        request_id=request_id,
        details={"name": pat.name, "token_prefix": pat.token_prefix},
    )
