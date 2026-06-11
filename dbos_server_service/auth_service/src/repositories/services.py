"""DAO для `PlatformService` — регистр платформенных сервисов."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.platform_service import PlatformService


class ServiceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, service_name: str) -> PlatformService | None:
        return await self._db.get(PlatformService, service_name)

    async def list_active(self) -> list[PlatformService]:
        result = await self._db.scalars(
            select(PlatformService).where(PlatformService.is_active.is_(True))
        )
        return list(result)

    async def exists(self, service_name: str) -> bool:
        return await self._db.scalar(
            select(PlatformService.service_name).where(
                PlatformService.service_name == service_name
            )
        ) is not None

    async def create(
        self, service_name: str, description: str | None
    ) -> PlatformService:
        svc = PlatformService(
            service_name=service_name,
            description=description,
        )
        self._db.add(svc)
        await self._db.flush()
        return svc

    async def deactivate(self, svc: PlatformService) -> None:
        svc.is_active = False
        await self._db.flush()
