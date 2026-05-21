"""CRUD дисков сервера. Изоляция отделов — через сервер-родитель."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.disk import DiskCreate, DiskResponse, DiskUpdate
from src.services import disk_service as svc

router = APIRouter(prefix="/servers/{server_id}/disks")


@router.get(
    "",
    response_model=PaginatedResponse[DiskResponse],
    summary="Список дисков сервера",
    description=(
        "Страница дисков указанного сервера, упорядоченных по `created_at DESC`. "
        "Сервер чужого department скрыт за 404."
    ),
    responses={
        403: {"description": "Нет роли с `view` на disk."},
        404: {"description": "Сервер не найден или принадлежит чужому department."},
    },
)
async def list_disks(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[DiskResponse]:
    """Список дисков. Доступ: `(disk, *, view)` + visibility сервера."""
    items, total = await svc.list_disks(
        db, identity, server_id, limit=limit, offset=offset,
    )
    return PaginatedResponse[DiskResponse](
        items=[DiskResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=DiskResponse,
    status_code=201,
    summary="Зарегистрировать диск (обычно через inventory worker)",
    description=(
        "Создаёт запись о диске. UNIQUE(server_id, device_name); "
        "одновременно у сервера может быть только один `is_system=true` диск."
    ),
    responses={
        201: {"description": "Диск создан."},
        403: {"description": "Нет роли с `create` на disk."},
        404: {"description": "Сервер не найден или чужой dept."},
        409: {"description": "Дубликат device_name или второй системный диск."},
    },
)
async def create_disk(
    server_id: str,
    body: DiskCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> DiskResponse:
    """Создание диска. Доступ: `(disk, *, create)`."""
    obj = await svc.create_disk(db, identity, server_id, body)
    return DiskResponse.model_validate(obj)


@router.get(
    "/{disk_id}",
    response_model=DiskResponse,
    summary="Получить диск",
    description="Карточка диска. Cross-dept скрыт за 404.",
    responses={
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Диск не найден / чужой dept."},
    },
)
async def get_disk(
    server_id: str,
    disk_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> DiskResponse:
    """Get диска. Доступ: `(disk, *, view)`."""
    obj = await svc.get_disk(db, identity, server_id, disk_id)
    return DiskResponse.model_validate(obj)


@router.patch(
    "/{disk_id}",
    response_model=DiskResponse,
    summary="Обновить поля диска",
    description=(
        "Частичное обновление (PATCH). Самый частый кейс — пометить диск как "
        "`is_system=true`. Дубль device_name / повторный system flag → 409."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Диск не найден / чужой dept."},
        409: {"description": "UNIQUE-конфликт по device_name или is_system."},
    },
)
async def update_disk(
    server_id: str,
    disk_id: str,
    body: DiskUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> DiskResponse:
    """PATCH диска. Доступ: `(disk, *, update)`."""
    obj = await svc.update_disk(db, identity, server_id, disk_id, body)
    return DiskResponse.model_validate(obj)


@router.delete(
    "/{disk_id}",
    response_model=OkResponse,
    summary="Удалить запись о диске",
    description=(
        "Hard-delete строки в БД. Физически диск не трогает. "
        "Требует роль с `delete`."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Диск не найден / чужой dept."},
    },
)
async def delete_disk(
    server_id: str,
    disk_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete диска. Доступ: `(disk, *, delete)`."""
    await svc.delete_disk(db, identity, server_id, disk_id)
    return OkResponse()
