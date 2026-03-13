from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.service_credential import ServiceCredential
from app.api.v1.schemas.service_credential import (
    ServiceCredentialCreate,
    ServiceCredentialUpdate,
)


async def list_credentials(db: AsyncSession) -> list[ServiceCredential]:
    stmt = select(ServiceCredential).order_by(ServiceCredential.service_name.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_credential_by_service(
    db: AsyncSession,
    service_name: str,
) -> ServiceCredential | None:
    stmt = select(ServiceCredential).where(ServiceCredential.service_name == service_name)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_or_update_credential(
    db: AsyncSession,
    payload: ServiceCredentialCreate,
    *,
    updated_by: str | None,
) -> tuple[ServiceCredential, bool]:
    existing = await get_credential_by_service(db, payload.service_name)
    if existing:
        existing.username = payload.username
        existing.password = payload.password
        existing.updated_by = updated_by
        await db.commit()
        await db.refresh(existing)
        return existing, False

    item = ServiceCredential(
        service_name=payload.service_name,
        username=payload.username,
        password=payload.password,
        updated_by=updated_by,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item, True


async def update_credential(
    db: AsyncSession,
    service_name: str,
    payload: ServiceCredentialUpdate,
    *,
    updated_by: str | None,
) -> ServiceCredential | None:
    item = await get_credential_by_service(db, service_name)
    if not item:
        return None

    if payload.username is not None:
        item.username = payload.username
    if payload.password is not None:
        item.password = payload.password
    item.updated_by = updated_by

    await db.commit()
    await db.refresh(item)
    return item


async def delete_credential(db: AsyncSession, service_name: str) -> bool:
    item = await get_credential_by_service(db, service_name)
    if not item:
        return False

    await db.delete(item)
    await db.commit()
    return True
