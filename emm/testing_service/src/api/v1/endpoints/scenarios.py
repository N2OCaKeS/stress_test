"""`/scenarios` — многостендовые сценарии: CRUD и превью;
запуск и остановка.

Права — матрица `test_definition` по отделу сценария; чтение — свой отдел.
"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.scenario import (
    ScenarioListResponse,
    ScenarioRunListResponse,
    ScenarioRunRequest,
    ScenarioRunResponse,
    ScenarioPreviewRequest,
    ScenarioPreviewResponse,
    ScenarioResponse,
    ScenarioWrite,
)
from src.services import scenario as svc
from src.services import scenario_queue

router = APIRouter(prefix="/scenarios")


@router.get("", response_model=ScenarioListResponse, summary="Сценарии отдела")
async def list_scenarios(
    identity: CurrentUserIdentity,
    department_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> ScenarioListResponse:
    return ScenarioListResponse(items=await svc.list_scenarios(db, identity, department_id))


@router.post("", response_model=ScenarioResponse, status_code=201, summary="Создать сценарий")
async def create_scenario(
    body: ScenarioWrite, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    return ScenarioResponse.model_validate(await svc.create_scenario(db, identity, body))


@router.get("/{scenario_id}", response_model=ScenarioResponse, summary="Сценарий целиком")
async def get_scenario(
    scenario_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    return ScenarioResponse.model_validate(await svc.get_scenario(db, identity, scenario_id))


@router.put("/{scenario_id}", response_model=ScenarioResponse, summary="Заменить сценарий целиком")
async def update_scenario(
    scenario_id: str, body: ScenarioWrite, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    return ScenarioResponse.model_validate(await svc.update_scenario(db, identity, scenario_id, body))


@router.delete("/{scenario_id}", status_code=204, summary="Удалить сценарий")
async def delete_scenario(
    scenario_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> Response:
    await svc.delete_scenario(db, identity, scenario_id)
    return Response(status_code=204)


@router.post(
    "/{scenario_id}/preview", response_model=ScenarioPreviewResponse,
    summary="Превью сценария: по каждому действию — что уйдёт воркеру",
)
async def preview_scenario(
    scenario_id: str, body: ScenarioPreviewRequest, identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ScenarioPreviewResponse:
    return ScenarioPreviewResponse.model_validate(await svc.preview_scenario(db, identity, scenario_id, body))


@router.post(
    "/{scenario_id}/runs", response_model=ScenarioRunResponse, status_code=201,
    summary="Запустить сценарий: бронь всех стендов, подготовка, действия по порядку",
)
async def start_scenario_run(
    scenario_id: str, body: ScenarioRunRequest, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioRunResponse:
    run = await scenario_queue.start_run(
        db, identity, scenario_id, os_version_id=body.os_version_id, kernel=body.kernel,
        mode=str(body.mode), debug=body.debug, stp_test_run_id=body.stp_test_run_id,
    )
    return ScenarioRunResponse.model_validate(await scenario_queue.serialize_run(db, run))


@router.get("/{scenario_id}/runs", response_model=ScenarioRunListResponse, summary="Запуски сценария")
async def list_scenario_runs(
    scenario_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioRunListResponse:
    runs = await scenario_queue.list_runs(db, identity, scenario_id)
    return ScenarioRunListResponse(items=[
        ScenarioRunResponse.model_validate(await scenario_queue.serialize_run(db, run)) for run in runs
    ])


runs_router = APIRouter(prefix="/scenario-runs")


@runs_router.get("/{run_id}", response_model=ScenarioRunResponse, summary="Запуск сценария")
async def get_scenario_run(
    run_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioRunResponse:
    run = await scenario_queue.get_run_or_404(db, identity, run_id)
    return ScenarioRunResponse.model_validate(await scenario_queue.serialize_run(db, run))


@runs_router.post(
    "/{run_id}/stop", response_model=ScenarioRunResponse,
    summary="Остановить сценарий: skip текущего действия, отмена остальных, стенды отпускаются",
)
async def stop_scenario_run(
    run_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ScenarioRunResponse:
    run = await scenario_queue.stop_run(db, identity, run_id)
    return ScenarioRunResponse.model_validate(await scenario_queue.serialize_run(db, run))

