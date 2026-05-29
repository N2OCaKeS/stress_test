"""CRUD каталога OS-версий. Каталог глобальный, без dept-привязки.

Чтение (list / get по id / get по имени) публичное — без auth. Запись
(create/update/delete) остаётся под матрицей прав. Anonymous-чтение
эмитит INFO-аудит (`os_version.list_anonymous` / `view_anonymous`) для
SIEM-видимости enumeration-попыток; поверх глобального rate-limit'а
повешен отдельный per-IP лимит `OS_VERSIONS_ANON_RATE_LIMIT`
(`settings.os_versions_anon_rate_limit`, default 100/minute) — на
authenticated запросы он не распространяется (см. `_anon_rate_limit_key`).
"""

from fastapi import APIRouter, Depends, Query, Request
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import BadRequestError
from src.core.limiter import endpoint_limiter
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.os_version import OsVersionCreate, OsVersionResponse, OsVersionUpdate
from src.services import audit_service
from src.services import os_version_service as svc

router = APIRouter(prefix="/os-versions")

_ANON_LIMIT = get_settings().os_versions_anon_rate_limit


def _is_anonymous(request: Request) -> bool:
    """Запрос пришёл без Bearer-токена.

    Authenticated GET тоже разрешён (read публичный), но audit-trail
    ведём только для anonymous — для них нет identity и нет других
    маркеров.
    """
    auth = request.headers.get("authorization") or ""
    return not auth.lower().startswith("bearer ")


def _anon_rate_limit_key(request: Request) -> str | None:
    """`key_func` для slowapi: возвращает client-IP только для anonymous.

    Authenticated клиент → `None`. slowapi трактует falsy key как «лимит не
    применять» (см. `extension.py: if all(args)`), поэтому authenticated
    read остаётся под одним только глобальным `global_rate_limit`.
    `exempt_when` тут не годится — slowapi-сигнатура для него — `() -> bool`
    (без request), а нам нужен contextual check.
    """
    if _is_anonymous(request):
        return get_remote_address(request)
    return None


@router.get(
    "",
    response_model=None,
    summary="Список OS-версий в каталоге",
    description=(
        "Глобальный каталог OS-версий. Публичный read — без авторизации.\n\n"
        "Два режима пагинации: cursor (`cursor=true` или `after=<token>`, "
        "envelope `{items, next_cursor, has_more}`) и legacy offset/limit "
        "(envelope `{items, total, limit, offset}`)."
    ),
    responses={
        200: {"description": "Страница каталога."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
    },
)
@endpoint_limiter.limit(_ANON_LIMIT, key_func=_anon_rate_limit_key)
async def list_os_versions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope."),
) -> PaginatedResponse[OsVersionResponse] | CursorPaginatedResponse[OsVersionResponse]:
    """List OS-версий. Публичный, без авторизации."""
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError
        try:
            items, next_cursor, has_more = await svc.list_os_versions_cursor(
                db, limit=limit, after=after,
            )
        except InvalidCursorError as exc:
            raise BadRequestError(
                error_code="INVALID_CURSOR",
                message="cursor 'after' is invalid",
                details={"hint": str(exc)},
            ) from exc
        if _is_anonymous(request):
            audit_service.emit(
                "os_version.list_anonymous",
                target_type="os_version",
                status="success", allowed=True,
                details={"caller_type": "anonymous", "page_size": len(items), "has_more": has_more},
            )
        return CursorPaginatedResponse[OsVersionResponse](
            items=[OsVersionResponse.model_validate(i) for i in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )
    items, total = await svc.list_os_versions(db, limit=limit, offset=offset)
    if _is_anonymous(request):
        audit_service.emit(
            "os_version.list_anonymous",
            target_type="os_version",
            status="success", allowed=True,
            details={"caller_type": "anonymous", "total": total},
        )
    return PaginatedResponse[OsVersionResponse](
        items=[OsVersionResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/by-name/{name}",
    response_model=OsVersionResponse,
    summary="Получить OS-версию по имени",
    description="Карточка версии по UNIQUE-имени. Публичный read — без авторизации.",
    responses={
        404: {"description": "Версия не найдена."},
    },
)
@endpoint_limiter.limit(_ANON_LIMIT, key_func=_anon_rate_limit_key)
async def get_os_version_by_name(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Get OS-версии по имени. Публичный, без авторизации."""
    obj = await svc.get_os_version_by_name(db, name)
    if _is_anonymous(request):
        audit_service.emit(
            "os_version.view_anonymous",
            target_id=obj.id,
            target_type="os_version",
            status="success", allowed=True,
            details={"caller_type": "anonymous", "lookup": "by_name", "name": name},
        )
    return OsVersionResponse.model_validate(obj)


@router.post(
    "",
    response_model=OsVersionResponse,
    status_code=201,
    summary="Зарегистрировать новую OS-версию",
    description=(
        "Добавляет новую запись в каталог. UNIQUE(name) — повтор → 409."
    ),
    responses={
        201: {"description": "Версия создана."},
        403: {"description": "Нет роли с `create`."},
        409: {"description": "Версия с таким `name` уже есть."},
    },
)
async def create_os_version(
    body: OsVersionCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Create OS-версии. Доступ: `(os_version, *, create)`."""
    obj = await svc.create_os_version(db, identity, body)
    return OsVersionResponse.model_validate(obj)


@router.get(
    "/{os_version_id}",
    response_model=OsVersionResponse,
    summary="Получить OS-версию",
    description="Карточка версии. Публичный read — без авторизации.",
    responses={
        404: {"description": "Версия не найдена."},
    },
)
@endpoint_limiter.limit(_ANON_LIMIT, key_func=_anon_rate_limit_key)
async def get_os_version(
    request: Request,
    os_version_id: str,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Get OS-версии по id. Публичный, без авторизации."""
    obj = await svc.get_os_version(db, os_version_id)
    if _is_anonymous(request):
        audit_service.emit(
            "os_version.view_anonymous",
            target_id=obj.id,
            target_type="os_version",
            status="success", allowed=True,
            details={"caller_type": "anonymous", "lookup": "by_id"},
        )
    return OsVersionResponse.model_validate(obj)


@router.patch(
    "/{os_version_id}",
    response_model=OsVersionResponse,
    summary="Обновить OS-версию",
    description=(
        "Частичное обновление. UNIQUE-конфликт по новому `name` → 409."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Версия не найдена."},
        409: {"description": "Конфликт UNIQUE(name)."},
    },
)
async def update_os_version(
    os_version_id: str,
    body: OsVersionUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """PATCH OS-версии. Доступ: `(os_version, *, update)`."""
    obj = await svc.update_os_version(db, identity, os_version_id, body)
    return OsVersionResponse.model_validate(obj)


@router.delete(
    "/{os_version_id}",
    response_model=OkResponse,
    summary="Удалить OS-версию",
    description=(
        "Hard-delete. FK `servers.os_version_id` ondelete=RESTRICT — "
        "если хоть один сервер ссылается, 409 OS_VERSION_IN_USE."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Версия не найдена."},
        409: {"description": "Версия используется хотя бы одним сервером."},
    },
)
async def delete_os_version(
    os_version_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete OS-версии. Доступ: `(os_version, *, delete)`."""
    await svc.delete_os_version(db, identity, os_version_id)
    return OkResponse()
