"""CRUD каталога категорий серверов по мощности. Каталог платформенный, без dept-привязки.

Чтение (список / карточка по id / карточка по коду) доступно любому
аутентифицированному актору — токен обязателен, но доступ департамента к
server_service тут не проверяется: категория это общая справочная величина,
а не бизнес-данные отдела. Анонимный запрос без bearer'а отбивается 401.
Запись (create/update/delete) остаётся под матрицей прав.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.server_category import (
    ServerCategoryCreate,
    ServerCategoryResponse,
    ServerCategoryUpdate,
)
from src.services import server_category as svc

router = APIRouter(prefix="/server-categories")


@router.get(
    "",
    response_model=PaginatedResponse[ServerCategoryResponse],
    summary="Список категорий серверов по мощности",
    description=(
        "Платформенный каталог категорий (`low_server` / `middle_server` / "
        "`high_server` / `workstation` и заведённые позже). Доступен любому "
        "аутентифицированному актору (токен обязателен). Envelope "
        "`{items, total, limit, offset}`."
    ),
    responses={
        200: {"description": "Страница каталога."},
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
    },
)
async def list_server_categories(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[ServerCategoryResponse]:
    """List категорий. Любой аутентифицированный актор."""
    items, total = await svc.list_server_categories(db, limit=limit, offset=offset)
    return PaginatedResponse[ServerCategoryResponse](
        items=[ServerCategoryResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/by-code/{code}",
    response_model=ServerCategoryResponse,
    summary="Получить категорию по коду",
    description="Карточка категории по UNIQUE-коду. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Категория не найдена."},
    },
)
async def get_server_category_by_code(
    code: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerCategoryResponse:
    """Get категории по коду. Любой аутентифицированный актор."""
    obj = await svc.get_server_category_by_code(db, code)
    return ServerCategoryResponse.model_validate(obj)


@router.post(
    "",
    response_model=ServerCategoryResponse,
    status_code=201,
    summary="Завести новую категорию по мощности",
    description="Добавляет запись в каталог. UNIQUE(code) — повтор → 409.",
    responses={
        201: {"description": "Категория создана."},
        403: {"description": "Нет роли с `create`."},
        409: {"description": "Категория с таким `code` уже есть."},
    },
)
async def create_server_category(
    body: ServerCategoryCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerCategoryResponse:
    """Create категории. Доступ: `(server_category, *, create)`."""
    obj = await svc.create_server_category(db, identity, body)
    return ServerCategoryResponse.model_validate(obj)


@router.get(
    "/{category_id}",
    response_model=ServerCategoryResponse,
    summary="Получить категорию",
    description="Карточка категории. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Категория не найдена."},
    },
)
async def get_server_category(
    category_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerCategoryResponse:
    """Get категории по id. Любой аутентифицированный актор."""
    obj = await svc.get_server_category(db, category_id)
    return ServerCategoryResponse.model_validate(obj)


@router.patch(
    "/{category_id}",
    response_model=ServerCategoryResponse,
    summary="Обновить категорию",
    description="Частичное обновление. UNIQUE-конфликт по новому `code` → 409.",
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Категория не найдена."},
        409: {"description": "Конфликт UNIQUE(code)."},
    },
)
async def update_server_category(
    category_id: str,
    body: ServerCategoryUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerCategoryResponse:
    """PATCH категории. Доступ: `(server_category, *, update)`."""
    obj = await svc.update_server_category(db, identity, category_id, body)
    return ServerCategoryResponse.model_validate(obj)


@router.delete(
    "/{category_id}",
    response_model=OkResponse,
    summary="Удалить категорию",
    description=(
        "Hard-delete. FK `servers.category_id` ondelete=RESTRICT — если хоть "
        "один сервер отнесён к этой категории, 409 SERVER_CATEGORY_IN_USE."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Категория не найдена."},
        409: {"description": "Категория используется хотя бы одним сервером."},
    },
)
async def delete_server_category(
    category_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete категории. Доступ: `(server_category, *, delete)`."""
    await svc.delete_server_category(db, identity, category_id)
    return OkResponse()
