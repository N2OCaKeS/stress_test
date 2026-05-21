"""CRUD каталога OS-версий. Каталог глобальный, без dept-привязки."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.os_version import OsVersionCreate, OsVersionResponse, OsVersionUpdate
from src.services import os_version_service as svc

router = APIRouter(prefix="/os-versions")


@router.get(
    "",
    response_model=PaginatedResponse[OsVersionResponse],
    summary="Список OS-версий в каталоге",
    description=(
        "Глобальный каталог OS-версий. Read доступен всем носителям "
        "`(os_version, *, view)`."
    ),
    responses={
        403: {"description": "Нет роли с `view`."},
    },
)
async def list_os_versions(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[OsVersionResponse]:
    """List OS-версий. Доступ: `(os_version, *, view)`."""
    items, total = await svc.list_os_versions(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[OsVersionResponse](
        items=[OsVersionResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


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
    description="Карточка версии.",
    responses={
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Версия не найдена."},
    },
)
async def get_os_version(
    os_version_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OsVersionResponse:
    """Get OS-версии. Доступ: `(os_version, *, view)`."""
    obj = await svc.get_os_version(db, identity, os_version_id)
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
