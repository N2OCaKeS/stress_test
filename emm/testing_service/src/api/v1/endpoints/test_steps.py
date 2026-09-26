"""Шаги многоступенчатого теста.

Смонтирован под `/test-definitions/{test_id}/steps`. Как и слоты команды,
шаги — часть редактирования теста: чтение наследует видимость теста,
запись проверяет `(test_definition, *, update)`. Слоты шага — через
`/test-definitions/{test_id}/args?step_id=…` (`test_command_args.py`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.test_step import TestStepCreate, TestStepOrder, TestStepResponse, TestStepUpdate
from src.services import test_step as svc

router = APIRouter(prefix="/test-definitions/{test_id}/steps")


@router.get(
    "",
    response_model=list[TestStepResponse],
    summary="Шаги теста",
    description="Шаги теста по порядку. У одношагового теста — один шаг. Видимость — как у самого теста.",
    responses={403: {"description": "Тест чужого отдела."}, 404: {"description": "Тест не найден."}},
)
async def list_steps(
    test_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[TestStepResponse]:
    return [TestStepResponse.model_validate(s) for s in await svc.list_steps(db, identity, test_id)]


@router.post(
    "",
    response_model=TestStepResponse,
    status_code=201,
    summary="Добавить шаг",
    description=(
        "Новый шаг в конец (или на `position`). `copy_args_from_step_id` — скопировать слоты "
        "команды другого шага этого теста. Первый шаг обязан быть `run_mode=full`."
    ),
    responses={
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "TEST_DEFINITION_NOT_FOUND / TEST_STEP_NOT_FOUND (шаг-источник слотов)."},
        422: {"description": "TEST_STEP_FIRST_MUST_BE_FULL; неверная настройка стенда."},
    },
)
async def create_step(
    test_id: str, body: TestStepCreate, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> TestStepResponse:
    return TestStepResponse.model_validate(await svc.create_step(db, identity, test_id, body))


@router.put(
    "/order",
    response_model=list[TestStepResponse],
    summary="Переставить шаги",
    description="`step_ids` — все шаги теста в новом порядке.",
    responses={
        403: {"description": "Нет `update` на test_definition."},
        409: {"description": "TEST_STEP_ORDER_STALE — список шагов устарел."},
        422: {"description": "TEST_STEP_ORDER_DUPLICATE_IDS; TEST_STEP_FIRST_MUST_BE_FULL."},
    },
)
async def reorder_steps(
    test_id: str, body: TestStepOrder, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[TestStepResponse]:
    steps = await svc.reorder_steps(db, identity, test_id, body.step_ids)
    return [TestStepResponse.model_validate(s) for s in steps]


@router.patch(
    "/{step_id}",
    response_model=TestStepResponse,
    summary="Изменить шаг",
    description="Имя, `starter_suffix`, `run_mode`, настройка стенда (`stand_setup: null` — убрать).",
    responses={
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "Тест или шаг не найден."},
        422: {"description": "TEST_STEP_FIRST_MUST_BE_FULL; неверная настройка стенда."},
    },
)
async def update_step(
    test_id: str, step_id: str, body: TestStepUpdate, identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestStepResponse:
    return TestStepResponse.model_validate(await svc.update_step(db, identity, test_id, step_id, body))


@router.delete(
    "/{step_id}",
    response_model=OkResponse,
    summary="Удалить шаг",
    description="Удаляет шаг вместе с его слотами команды. Последний шаг удалить нельзя.",
    responses={
        403: {"description": "Нет `update` на test_definition."},
        404: {"description": "Тест или шаг не найден."},
        409: {"description": "TEST_STEP_LAST — у теста должен остаться хотя бы один шаг."},
        422: {"description": "TEST_STEP_FIRST_MUST_BE_FULL — первым стал бы rerun-шаг."},
    },
)
async def delete_step(
    test_id: str, step_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await svc.delete_step(db, identity, test_id, step_id)
    return OkResponse()
