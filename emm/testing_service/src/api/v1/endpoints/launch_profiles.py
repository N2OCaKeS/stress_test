"""`/launch-profiles` — профили запуска теста.

Чтение — свой отдел (видны профили отдела и общие). Запись — профиль отдела:
`launch_profile:update` или department_admin отдела; общий профиль — только
по матрице. Правка содержимого — всегда новая версия.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.launch_profile import (
    LaunchProfileCreate,
    LaunchProfileListResponse,
    LaunchProfileResponse,
    LaunchProfileUpdate,
    LaunchProfileVersionInput,
    LaunchProfileVersionResponse,
)
from src.services import launch_profile as svc

router = APIRouter(prefix="/launch-profiles")


@router.get("", response_model=LaunchProfileListResponse, summary="Профили запуска отдела и общие")
async def list_launch_profiles(
    identity: CurrentUserIdentity,
    department_id: str = Query(..., max_length=64),
    db: AsyncSession = Depends(get_db),
) -> LaunchProfileListResponse:
    return LaunchProfileListResponse(items=await svc.list_for(db, identity, department_id))


@router.post("", response_model=LaunchProfileResponse, status_code=201, summary="Создать профиль запуска")
async def create_launch_profile(
    body: LaunchProfileCreate, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> LaunchProfileResponse:
    return LaunchProfileResponse(**await svc.create(db, identity, body))


@router.get("/{profile_id}", response_model=LaunchProfileResponse, summary="Профиль запуска")
async def get_launch_profile(
    profile_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> LaunchProfileResponse:
    return LaunchProfileResponse(**await svc.get_for(db, identity, profile_id))


@router.patch("/{profile_id}", response_model=LaunchProfileResponse, summary="Переименовать / сделать по умолчанию")
async def update_launch_profile(
    profile_id: str, body: LaunchProfileUpdate, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> LaunchProfileResponse:
    return LaunchProfileResponse(**await svc.update(db, identity, profile_id, body))


@router.get(
    "/{profile_id}/versions", response_model=list[LaunchProfileVersionResponse],
    summary="История версий профиля (новые первыми)",
)
async def list_launch_profile_versions(
    profile_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[LaunchProfileVersionResponse]:
    return [LaunchProfileVersionResponse.model_validate(v) for v in await svc.list_versions_for(db, identity, profile_id)]


@router.post(
    "/{profile_id}/versions", response_model=LaunchProfileVersionResponse, status_code=201,
    summary="Новая версия профиля (становится действующей)",
)
async def create_launch_profile_version(
    profile_id: str, body: LaunchProfileVersionInput, identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> LaunchProfileVersionResponse:
    return LaunchProfileVersionResponse.model_validate(await svc.add_version(db, identity, profile_id, body))
