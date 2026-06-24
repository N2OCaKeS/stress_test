"""CRUD макросов консоли — личные + системные (общие в отделе).

Личный макрос (`is_system=false`) заводит любой аутентифицированный
пользователь, привязывается к нему; видит/правит/удаляет только владелец.
Системный (`is_system=true`) — общий в отделе, создаёт/правит/удаляет только
department_admin своего отдела. GET отдаёт мои личные + системные моего отдела.

Доступ к серверам тут ни при чём — это пользовательский справочник команд, а
не операция над сервером. Используем `CurrentUserIdentity` (department-bound
identity, OAuth m2m отбивается). platform-admin'ы (`account_admin`/`loging_admin`)
отрезаются `platform_admin_guard` middleware как на любом business endpoint'е.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.console_macro import (
    ConsoleMacroCreate,
    ConsoleMacroResponse,
    ConsoleMacroUpdate,
)
from src.services import console_macro as svc

router = APIRouter(prefix="/console-macros")


@router.get(
    "",
    response_model=list[ConsoleMacroResponse],
    summary="Список макросов консоли",
    description=(
        "Личные макросы вызывающего + системные его отдела. Сортировка: сначала "
        "личные, потом системные, внутри группы по `display_order`."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
    },
)
async def list_console_macros(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[ConsoleMacroResponse]:
    """List макросов. Мои личные + системные отдела."""
    items = await svc.list_macros(db, identity)
    return [ConsoleMacroResponse.model_validate(i) for i in items]


@router.post(
    "",
    response_model=ConsoleMacroResponse,
    status_code=201,
    summary="Создать макрос консоли",
    description=(
        "`is_system=false` — личный (привязывается к вызывающему). "
        "`is_system=true` — системный (общий в отделе), только department_admin "
        "своего отдела, иначе 403."
    ),
    responses={
        201: {"description": "Макрос создан."},
        403: {"description": "Системный макрос без прав department_admin."},
    },
)
async def create_console_macro(
    body: ConsoleMacroCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ConsoleMacroResponse:
    """Create макроса. Личный — любой; системный — department_admin отдела."""
    obj = await svc.create_macro(db, identity, body)
    return ConsoleMacroResponse.model_validate(obj)


@router.patch(
    "/{macro_id}",
    response_model=ConsoleMacroResponse,
    summary="Обновить макрос консоли",
    description=(
        "Личный — только владелец; системный — department_admin отдела. Чужой "
        "макрос → 404 (существование не светится)."
    ),
    responses={
        403: {"description": "Нет прав на изменение этого макроса."},
        404: {"description": "Макрос не найден или скрыт от вызывающего."},
    },
)
async def update_console_macro(
    macro_id: str,
    body: ConsoleMacroUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ConsoleMacroResponse:
    """PATCH макроса. Личный — владелец; системный — department_admin."""
    obj = await svc.update_macro(db, identity, macro_id, body)
    return ConsoleMacroResponse.model_validate(obj)


@router.delete(
    "/{macro_id}",
    response_model=OkResponse,
    summary="Удалить макрос консоли",
    description=(
        "Те же правила доступа, что у PATCH. Чужой макрос → 404."
    ),
    responses={
        403: {"description": "Нет прав на удаление этого макроса."},
        404: {"description": "Макрос не найден или скрыт от вызывающего."},
    },
)
async def delete_console_macro(
    macro_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete макроса. Личный — владелец; системный — department_admin."""
    await svc.delete_macro(db, identity, macro_id)
    return OkResponse()
