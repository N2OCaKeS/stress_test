"""`/provisioning-profiles` — профили подготовки стенда.

Чтение — свой отдел (видны профили отдела и общие). Запись — профиль отдела:
`provisioning_profile:update` или department_admin отдела; общий — только по
матрице.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.provisioning_profile import (
    ProvisioningProfileCreate,
    ProvisioningProfileListResponse,
    ProvisioningProfileResponse,
    ProvisioningProfileUpdate,
)
from src.services import provisioning_profile as svc

router = APIRouter(prefix="/provisioning-profiles")


@router.get("", response_model=ProvisioningProfileListResponse, summary="Профили подготовки отдела и общие")
async def list_provisioning_profiles(
    identity: CurrentUserIdentity,
    department_id: str = Query(..., max_length=64),
    db: AsyncSession = Depends(get_db),
) -> ProvisioningProfileListResponse:
    items = await svc.list_for(db, identity, department_id)
    return ProvisioningProfileListResponse(items=[ProvisioningProfileResponse.model_validate(p) for p in items])


@router.post("", response_model=ProvisioningProfileResponse, status_code=201, summary="Создать профиль подготовки")
async def create_provisioning_profile(
    body: ProvisioningProfileCreate, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> ProvisioningProfileResponse:
    return ProvisioningProfileResponse.model_validate(await svc.create(db, identity, body))


@router.patch("/{profile_id}", response_model=ProvisioningProfileResponse, summary="Изменить профиль подготовки")
async def update_provisioning_profile(
    profile_id: str, body: ProvisioningProfileUpdate, identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ProvisioningProfileResponse:
    return ProvisioningProfileResponse.model_validate(await svc.update_profile(db, identity, profile_id, body))
