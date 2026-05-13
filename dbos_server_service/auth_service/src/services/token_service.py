"""Personal access token workflows."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.core.security import generate_pat
from src.repositories.departments import DepartmentRepository
from src.repositories.tokens import TokenRepository
from src.schemas.tokens import PATCreateResponse, PATListItem
from src.services import audit_service
from src.utils.time import is_expired


async def create_pat(
    db: AsyncSession,
    actor_id: str,
    name: str,
    allowed_services: list[str],
    expires_at=None,
    request_id: str | None = None,
) -> PATCreateResponse:
    token_repo = TokenRepository(db)
    dept_repo = DepartmentRepository(db)

    if await token_repo.exists_name(actor_id, name):
        raise ConflictError(error_code="TOKEN_NAME_ALREADY_EXISTS", message=f"Token '{name}' already exists")

    raw, prefix, token_hash = generate_pat()
    pat = await token_repo.create(
        user_id=actor_id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        allowed_services=allowed_services,
        expires_at=expires_at,
    )
    await db.commit()
    # raw PAT передаётся в details — sanitizer заменит на <TOKEN> (по эвристике dbos_pat_…).
    audit_service.emit(
        "pat.create", actor_id, target_id=pat.id, target_type="pat",
        request_id=request_id,
        details={
            "name": name,
            "token_prefix": prefix,
            "token": raw,
            "allowed_services": list(allowed_services),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return PATCreateResponse(token_id=pat.id, token=raw, name=pat.name, expires_at=pat.expires_at)


async def list_pats(db: AsyncSession, actor_id: str, request_id: str | None = None) -> list[PATListItem]:
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
