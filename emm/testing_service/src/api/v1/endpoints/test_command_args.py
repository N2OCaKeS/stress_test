"""CRUD слотов конструктора команд теста (§3.2-3.3 плана миграции).

Смонтирован под `/test-definitions/{test_id}/args`. Слоты не заводят
отдельную защищаемую сущность верхнего уровня — read открыт любому
аутентифицированному актору (как и сам тест), write проверяет
`(test_definition, *, update)`, потому что редактирование команды это часть
редактирования теста.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.test_command_arg import (
    TestCommandArgCreate,
    TestCommandArgResponse,
    TestCommandArgUpdate,
)
from src.services import test_command_arg as svc

router = APIRouter(prefix="/test-definitions/{test_id}/args")


@router.get(
    "",
    response_model=list[TestCommandArgResponse],
    summary="Слоты команды теста",
    description="Все слоты теста, упорядоченные по `position`. Любой аутентифицированный актор.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Тест не найден."},
    },
)
async def list_command_args(
    test_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[TestCommandArgResponse]:
    """List слотов теста по порядку. Любой аутентифицированный актор."""
    items = await svc.list_command_args(db, test_id)
    return [TestCommandArgResponse.model_validate(i) for i in items]


@router.post(
    "",
    response_model=TestCommandArgResponse,
    status_code=201,
    summary="Добавить слот в команду",
    description=(
        "Добавляет слот в конец списка (или на явную `position`). "
        "`kind=literal` требует `literal_value` и пустой `variable_id`; "
        "`kind=variable` требует `variable_id` и пустой `literal_value`."
    ),
    responses={
        201: {"description": "Слот добавлен."},
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "Тест или переменная не найдены."},
        422: {"description": "COMMAND_ARG_KIND_MISMATCH — literal/variable взаимоисключение нарушено."},
    },
)
async def create_command_arg(
    test_id: str,
    body: TestCommandArgCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestCommandArgResponse:
    """Create слота. Доступ: `(test_definition, *, update)`."""
    obj = await svc.create_command_arg(db, identity, test_id, body)
    return TestCommandArgResponse.model_validate(obj)


@router.patch(
    "/{arg_id}",
    response_model=TestCommandArgResponse,
    summary="Обновить слот",
    description="Частичное обновление: значение, тип или позиция слота.",
    responses={
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "Тест, слот или переменная не найдены."},
        422: {"description": "COMMAND_ARG_KIND_MISMATCH — literal/variable взаимоисключение нарушено."},
    },
)
async def update_command_arg(
    test_id: str,
    arg_id: str,
    body: TestCommandArgUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestCommandArgResponse:
    """PATCH слота. Доступ: `(test_definition, *, update)`."""
    obj = await svc.update_command_arg(db, identity, test_id, arg_id, body)
    return TestCommandArgResponse.model_validate(obj)


@router.delete(
    "/{arg_id}",
    response_model=OkResponse,
    summary="Удалить слот",
    description="Убирает слот из команды теста.",
    responses={
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "Тест или слот не найдены."},
    },
)
async def delete_command_arg(
    test_id: str,
    arg_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete слота. Доступ: `(test_definition, *, update)`."""
    await svc.delete_command_arg(db, identity, test_id, arg_id)
    return OkResponse()
