"""CRUD каталога глобальных переменных конструктора команд.

Каталог платформенный, без dept-привязки (§10 плана миграции). Чтение
(список / карточка по id / карточка по коду / резолв choices) доступно
любому аутентифицированному актору — токен обязателен, но доступ отдела к
testing_service тут не проверяется: это общий справочник, а не бизнес-данные
отдела. Анонимный запрос без bearer'а отбивается 401. Запись
(create/update/delete) — под матрицей прав.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.global_variable import (
    ChoiceItem,
    ChoicesResponse,
    GlobalVariableCreate,
    GlobalVariableResponse,
    GlobalVariableUpdate,
)
from src.services import global_variable as svc

router = APIRouter(prefix="/global-variables")


@router.get(
    "",
    response_model=PaginatedResponse[GlobalVariableResponse],
    summary="Список глобальных переменных",
    description=(
        "Платформенный каталог переменных конструктора команд (`RC`, `STAND`, "
        "`KERNEL`, `MODE`, `TESTENV`, `HOME_DIR`, `TEST_USER`, "
        "`TEST_PASSWORD`, `TEST_SSH_KEY` и заведённые позже). Доступен любому "
        "аутентифицированному актору. Envelope `{items, total, limit, offset}`."
    ),
    responses={
        200: {"description": "Страница каталога."},
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
    },
)
async def list_global_variables(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[GlobalVariableResponse]:
    """List переменных. Любой аутентифицированный актор."""
    items, total = await svc.list_global_variables(db, limit=limit, offset=offset)
    return PaginatedResponse[GlobalVariableResponse](
        items=[GlobalVariableResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/by-code/{code}",
    response_model=GlobalVariableResponse,
    summary="Получить переменную по коду",
    description="Карточка переменной по UNIQUE-коду. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Переменная не найдена."},
    },
)
async def get_global_variable_by_code(
    code: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> GlobalVariableResponse:
    """Get переменной по коду. Любой аутентифицированный актор."""
    obj = await svc.get_global_variable_by_code(db, code)
    return GlobalVariableResponse.model_validate(obj)


@router.post(
    "",
    response_model=GlobalVariableResponse,
    status_code=201,
    summary="Завести новую переменную",
    description=(
        "Добавляет запись в каталог. UNIQUE(code) — повтор → 409. "
        "`choices_source` проверяется на форму (`static:<json>` / "
        "`dynamic:<известный резолвер>`), содержимое `static:` разбирается "
        "только при резолве."
    ),
    responses={
        201: {"description": "Переменная создана."},
        403: {"description": "Нет роли с `create`."},
        409: {"description": "Переменная с таким `code` уже есть."},
        422: {"description": "CHOICES_SOURCE_INVALID — непонятный источник значений."},
    },
)
async def create_global_variable(
    body: GlobalVariableCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> GlobalVariableResponse:
    """Create переменной. Доступ: `(global_variable, *, create)`."""
    obj = await svc.create_global_variable(db, identity, body)
    return GlobalVariableResponse.model_validate(obj)


@router.get(
    "/{variable_id}",
    response_model=GlobalVariableResponse,
    summary="Получить переменную",
    description="Карточка переменной. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Переменная не найдена."},
    },
)
async def get_global_variable(
    variable_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> GlobalVariableResponse:
    """Get переменной по id. Любой аутентифицированный актор."""
    obj = await svc.get_global_variable(db, variable_id)
    return GlobalVariableResponse.model_validate(obj)


@router.get(
    "/{variable_id}/choices",
    response_model=ChoicesResponse,
    summary="Резолв списка значений переменной",
    description=(
        "Разворачивает `choices_source` в список `{value, label}` **в момент "
        "запроса**: `static:<json>` парсится на лету, `dynamic:<resolver>` "
        "идёт живым запросом к источнику (например, каталог OS-версий в "
        "server_service). Параметризованные резолверы читают query-параметры: "
        "`dynamic:kernels` требует `os_version_id`."
    ),
    responses={
        200: {"description": "Список значений на момент запроса."},
        400: {"description": "CHOICES_PARAM_REQUIRED — резолверу не хватает параметра."},
        404: {"description": "Переменная не найдена."},
        422: {
            "description": (
                "CHOICES_SOURCE_NOT_SET / CHOICES_SOURCE_INVALID / "
                "CHOICES_RESOLVER_UNKNOWN."
            ),
        },
        503: {"description": "Источник значений недоступен (server_service)."},
    },
)
async def get_global_variable_choices(
    variable_id: str,
    request: Request,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> ChoicesResponse:
    """Резолв choices. Любой аутентифицированный актор.

    Query-параметры прокидываются в резолвер как есть — набор зависит от
    источника, фиксировать его в сигнатуре нельзя.
    """
    params = dict(request.query_params)
    items, source = await svc.resolve_choices(db, variable_id, params)
    return ChoicesResponse(
        items=[ChoiceItem(**item) for item in items],
        choices_source=source,
    )


@router.patch(
    "/{variable_id}",
    response_model=GlobalVariableResponse,
    summary="Обновить переменную",
    description="Частичное обновление. UNIQUE-конфликт по новому `code` → 409.",
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Переменная не найдена."},
        409: {"description": "Конфликт UNIQUE(code)."},
        422: {"description": "CHOICES_SOURCE_INVALID — непонятный источник значений."},
    },
)
async def update_global_variable(
    variable_id: str,
    body: GlobalVariableUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> GlobalVariableResponse:
    """PATCH переменной. Доступ: `(global_variable, *, update)`."""
    obj = await svc.update_global_variable(db, identity, variable_id, body)
    return GlobalVariableResponse.model_validate(obj)


@router.delete(
    "/{variable_id}",
    response_model=OkResponse,
    summary="Удалить переменную",
    description=(
        "Hard-delete. Слоты команд, ссылающиеся на переменную, живут в другом "
        "домене — связность проверяется там, когда каталог тестов появится."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Переменная не найдена."},
    },
)
async def delete_global_variable(
    variable_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete переменной. Доступ: `(global_variable, *, delete)`."""
    await svc.delete_global_variable(db, identity, variable_id)
    return OkResponse()
