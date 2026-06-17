"""CRUD каталога OS-версий. Каталог глобальный, без dept-привязки.

Чтение (list / get по id / get по имени) доступно любому аутентифицированному
актору — токен обязателен, но проверка доступа департамента к server_service
здесь не нужна (каталог общий, не бизнес-данные отдела). Анонимный запрос без
bearer'а отбивается 401. Запись (create/update/delete) остаётся под матрицей
прав.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.os_version import OsVersionCreate, OsVersionResponse, OsVersionUpdate
from src.services import os_version_service as svc

router = APIRouter(prefix="/os-versions")


@router.get(
    "",
    response_model=(
        PaginatedResponse[OsVersionResponse]
        | CursorPaginatedResponse[OsVersionResponse]
    ),
    summary="Список OS-версий в каталоге",
    description=(
        "Глобальный каталог OS-версий. Доступен любому аутентифицированному "
        "актору (токен обязателен).\n\n"
        "Два режима пагинации: cursor (`cursor=true` или `after=<token>`, "
        "envelope `{items, next_cursor, has_more}`) и legacy offset/limit "
        "(envelope `{items, total, limit, offset}`)."
    ),
    responses={
        200: {"description": "Страница каталога."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
    },
)
async def list_os_versions(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope."),
) -> PaginatedResponse[OsVersionResponse] | CursorPaginatedResponse[OsVersionResponse]:
    """List OS-версий. Любой аутентифицированный актор."""
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError, to_bad_request
        try:
            items, next_cursor, has_more = await svc.list_os_versions_cursor(
                db, limit=limit, after=after,
            )
        except InvalidCursorError as exc:
            raise to_bad_request(exc) from exc
        return CursorPaginatedResponse[OsVersionResponse](
            items=[OsVersionResponse.model_validate(i) for i in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )
    items, total = await svc.list_os_versions(db, limit=limit, offset=offset)
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
    description="Карточка версии по UNIQUE-имени. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Версия не найдена."},
    },
)
async def get_os_version_by_name(
    name: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Get OS-версии по имени. Любой аутентифицированный актор."""
    obj = await svc.get_os_version_by_name(db, name)
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
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Create OS-версии. Доступ: `(os_version, *, create)`."""
    obj = await svc.create_os_version(db, identity, body)
    return OsVersionResponse.model_validate(obj)


@router.get(
    "/{os_version_id}",
    response_model=OsVersionResponse,
    summary="Получить OS-версию",
    description="Карточка версии. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Версия не найдена."},
    },
)
async def get_os_version(
    os_version_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Get OS-версии по id. Любой аутентифицированный актор."""
    obj = await svc.get_os_version(db, os_version_id)
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
    identity: CurrentUserIdentity,
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
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete OS-версии. Доступ: `(os_version, *, delete)`."""
    await svc.delete_os_version(db, identity, os_version_id)
    return OkResponse()
