"""`/zephyr-status-mappings` — маппинг статусов Zephyr → исход теста.

GET — действующий набор отдела (свой или по умолчанию), читает свой отдел.
PUT заменяет набор отдела целиком, DELETE возвращает к набору по умолчанию;
права — `(department_test_settings, *, update)` или department_admin отдела.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.zephyr_status_mapping import ZephyrStatusMappingResponse, ZephyrStatusMappingUpdate
from src.services import zephyr_verdict as svc

router = APIRouter(prefix="/zephyr-status-mappings")


@router.get(
    "/{department_id}",
    response_model=ZephyrStatusMappingResponse,
    summary="Маппинг статусов Zephyr отдела",
    description=(
        "Какой статус тест-кейса в Zephyr считается пройденным, проваленным или "
        "незавершённым. `is_default=true` — у отдела своих строк нет."
    ),
    responses={403: {"description": "DEPARTMENT_ISOLATION — чужой отдел."}},
)
async def get_zephyr_status_mapping(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ZephyrStatusMappingResponse:
    return ZephyrStatusMappingResponse(**await svc.get_for(db, identity, department_id))


@router.put(
    "/{department_id}",
    response_model=ZephyrStatusMappingResponse,
    summary="Заменить маппинг статусов Zephyr отдела",
    description="Заменяет набор отдела целиком. Статусы уникальны без учёта регистра.",
    responses={403: {"description": "Нет права `department_test_settings:update`."}},
)
async def put_zephyr_status_mapping(
    department_id: str,
    body: ZephyrStatusMappingUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ZephyrStatusMappingResponse:
    return ZephyrStatusMappingResponse(**await svc.replace_for(db, identity, department_id, body))


@router.delete(
    "/{department_id}",
    response_model=ZephyrStatusMappingResponse,
    summary="Сбросить маппинг статусов Zephyr к набору по умолчанию",
    responses={403: {"description": "Нет права `department_test_settings:update`."}},
)
async def reset_zephyr_status_mapping(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ZephyrStatusMappingResponse:
    return ZephyrStatusMappingResponse(**await svc.reset_for(db, identity, department_id))
