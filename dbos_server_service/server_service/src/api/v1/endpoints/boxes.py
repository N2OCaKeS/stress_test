"""Эндпоинты каталога боксов-заготовок: /boxes (пер-департамент CRUD).

Бокс — образ-заготовка для создания ВМ: формат, источник скачивания,
предустановленный пользователь и список ОС/снимков на диске. Доступ гейтится
тип-wide матрицей `(box, *)` плюс изоляцией отдела. Пароль образного
пользователя отдаётся только держателю `view_password`.

Скачивание/импорт боксов по URL — отдельная операция; эти эндпоинты только
ведут каталог.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.box import BoxCreate, BoxResponse, BoxUpdate
from src.schemas.common import OkResponse, PaginatedResponse
from src.services import box_service as svc

router = APIRouter(prefix="/boxes")


def _to_response(box, password_b64: str | None = None) -> BoxResponse:
    """Собрать BoxResponse из ORM-строки, подставив раскрытый пароль (если есть)."""
    resp = BoxResponse.model_validate(box)
    if password_b64 is not None:
        resp.base_user_password_b64 = password_b64
    return resp


@router.get(
    "",
    response_model=PaginatedResponse[BoxResponse],
    summary="Список боксов своего отдела",
    description=(
        "Возвращает страницу боксов-заготовок отдела вызывающего. Без `view` "
        "на `box` — 403. Пароль образного пользователя в листинге не отдаётся."
    ),
    responses={
        200: {"description": "Страница боксов."},
        401: {"description": "Нет/невалидный bearer-токен."},
        403: {"description": "Нет роли с `view` на box."},
    },
)
async def list_boxes(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[BoxResponse]:
    """List-эндпоинт. Доступ: `(box, view)`."""
    items, total = await svc.list_boxes(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[BoxResponse](
        items=[_to_response(b) for b in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=BoxResponse,
    status_code=201,
    summary="Завести бокс-заготовку в своём отделе",
    description=(
        "Создаёт каталожную запись бокса. Пароль образного пользователя "
        "принимается как `base_user_password_b64` и шифруется через "
        "`secrets_service.encrypt()` ДО записи; в ответе не возвращается. "
        "`department_id` обязан совпадать с отделом вызывающего. Имя уникально "
        "в отделе → повтор → 409 BOX_DUPLICATE."
    ),
    responses={
        201: {"description": "Бокс создан."},
        403: {"description": "Нет `create` на box."},
        409: {"description": "DEPARTMENT_ISOLATION / BOX_DUPLICATE."},
    },
)
async def create_box(
    body: BoxCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    """Create-эндпоинт. Доступ: `(box, create)`."""
    box = await svc.create_box(db, identity, body)
    return _to_response(box)


@router.get(
    "/{box_id}",
    response_model=BoxResponse,
    summary="Карточка бокса",
    description=(
        "Карточка бокса своего отдела. Доступна по `view` или `view_password`; "
        "держателю `view_password` в ответе присутствует `base_user_password_b64` "
        "(base64 plaintext). Чужой отдел / несуществующий бокс → 404 BOX_NOT_FOUND."
    ),
    responses={
        200: {"description": "Карточка бокса."},
        403: {"description": "Нет `view`/`view_password` на box."},
        404: {"description": "BOX_NOT_FOUND — бокс не найден / чужой отдел."},
    },
)
async def get_box(
    box_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    """Get-эндпоинт. Доступ: `(box, view)` или `(box, view_password)`."""
    box, password_b64 = await svc.get_box(db, identity, box_id)
    return _to_response(box, password_b64)


@router.patch(
    "/{box_id}",
    response_model=BoxResponse,
    summary="Изменить бокс (частично)",
    description=(
        "Меняет переданные поля бокса. `base_user_password_b64` задаёт/меняет "
        "пароль образного пользователя (шифруется). `department_id` не "
        "редактируется. Чужой отдел → 404 BOX_NOT_FOUND, конфликт имени → 409."
    ),
    responses={
        200: {"description": "Бокс обновлён."},
        403: {"description": "Нет `update` на box."},
        404: {"description": "BOX_NOT_FOUND."},
        409: {"description": "BOX_DUPLICATE."},
    },
)
async def update_box(
    box_id: str,
    body: BoxUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    """Update-эндпоинт. Доступ: `(box, update)`."""
    box = await svc.update_box(db, identity, box_id, body)
    return _to_response(box)


@router.delete(
    "/{box_id}",
    response_model=OkResponse,
    summary="Удалить бокс",
    description=(
        "Удаляет каталожную запись бокса своего отдела. Чужой отдел / "
        "несуществующий → 404 BOX_NOT_FOUND."
    ),
    responses={
        200: {"description": "Бокс удалён."},
        403: {"description": "Нет `delete` на box."},
        404: {"description": "BOX_NOT_FOUND."},
    },
)
async def delete_box(
    box_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete-эндпоинт. Доступ: `(box, delete)`."""
    await svc.delete_box(db, identity, box_id)
    return OkResponse()
