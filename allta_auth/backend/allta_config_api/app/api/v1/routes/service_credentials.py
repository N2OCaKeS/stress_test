from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.crud.service_credential import (
    create_or_update_credential,
    delete_credential,
    get_credential_by_service,
    list_credentials,
    update_credential,
)
from app.api.v1.dependencies import AuthVerifyResponse, require_permission
from app.api.v1.schemas.service_credential import (
    ServiceCredentialCreate,
    ServiceCredentialRead,
    ServiceCredentialUpdate,
)
from app.db.session import get_db
from app.utils.config import settings

router = APIRouter()


@router.get(
    "/credentials",
    summary="List service credentials (requires permission)",
    response_model=list[ServiceCredentialRead],
)
async def list_service_credentials(
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_READ_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    return await list_credentials(db)


@router.get(
    "/credentials/{service_name}",
    summary="Get service credentials by service name (requires permission)",
    response_model=ServiceCredentialRead,
)
async def get_service_credential(
    service_name: str = Path(..., min_length=1, max_length=120),
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_READ_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    item = await get_credential_by_service(db, service_name.strip())
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return item


@router.post(
    "/credentials",
    summary="Create or update service credentials (requires permission)",
    response_model=ServiceCredentialRead,
)
async def upsert_service_credential(
    payload: ServiceCredentialCreate,
    response: Response,
    user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    normalized = ServiceCredentialCreate(
        service_name=payload.service_name.strip(),
        username=payload.username.strip(),
        password=payload.password.strip(),
    )
    if not normalized.service_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="service_name cannot be empty")

    item, created = await create_or_update_credential(
        db,
        normalized,
        updated_by=user.login,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return item


@router.patch(
    "/credentials/{service_name}",
    summary="Update service credentials (requires permission)",
    response_model=ServiceCredentialRead,
)
async def patch_service_credential(
    payload: ServiceCredentialUpdate,
    service_name: str = Path(..., min_length=1, max_length=120),
    user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    if payload.username is None and payload.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required: username or password",
        )

    normalized = ServiceCredentialUpdate(
        username=payload.username.strip() if payload.username is not None else None,
        password=payload.password.strip() if payload.password is not None else None,
    )
    if normalized.username == "" or normalized.password == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username/password cannot be empty",
        )

    item = await update_credential(
        db,
        service_name=service_name.strip(),
        payload=normalized,
        updated_by=user.login,
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return item


@router.delete(
    "/credentials/{service_name}",
    summary="Delete service credentials (requires permission)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_service_credential(
    service_name: str = Path(..., min_length=1, max_length=120),
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_credential(db, service_name.strip())
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return

