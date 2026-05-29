"""CRUD каталога OS-версий. Каталог глобальный, без dept-привязки.

Чтение (list / get по id / get по имени) публичное — без auth. Запись
(create/update/delete) остаётся под матрицей прав. Anonymous-чтение
эмитит INFO-аудит (`os_version.list_anonymous` / `view_anonymous`) для
SIEM-видимости enumeration-попыток; общий rate-limit middleware кладёт
потолок на частоту таких вызовов.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.os_version import OsVersionCreate, OsVersionResponse, OsVersionUpdate
from src.services import audit_service
from src.services import os_version_service as svc

router = APIRouter(prefix="/os-versions")


def _is_anonymous(request: Request) -> bool:
    """Запрос пришёл без Bearer-токена.

    Authenticated GET тоже разрешён (read публичный), но audit-trail
    ведём только для anonymous — для них нет identity и нет других
    маркеров.
    """
    auth = request.headers.get("authorization") or ""
    return not auth.lower().startswith("bearer ")


@router.get(
    "",
    response_model=PaginatedResponse[OsVersionResponse],
    summary="Список OS-версий в каталоге",
    description="Глобальный каталог OS-версий. Публичный read — без авторизации.",
)
async def list_os_versions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[OsVersionResponse]:
    """List OS-версий. Публичный, без авторизации."""
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
async def get_os_version_by_name(
    name: str,
    request: Request,
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
async def get_os_version(
    os_version_id: str,
    request: Request,
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
