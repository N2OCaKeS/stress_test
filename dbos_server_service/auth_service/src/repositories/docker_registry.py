"""DAO для `DepartmentDockerRegistry` — per-department конфиг Docker registry."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id


class DockerRegistryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_department(self, department_id: str) -> DepartmentDockerRegistry | None:
        return await self._db.scalar(
            select(DepartmentDockerRegistry).where(
                DepartmentDockerRegistry.department_id == department_id
            )
        )

    async def create(
        self,
        department_id: str,
        pull_policy: str,
        pull_user_ids: list[str],
        push_user_ids: list[str],
        created_by: str | None = None,
    ) -> DepartmentDockerRegistry:
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=department_id,
            is_enabled=True,
            pull_policy=pull_policy,
            pull_user_ids=pull_user_ids,
            push_user_ids=push_user_ids,
            created_by=created_by,
        )
        self._db.add(cfg)
        await self._db.flush()
        return cfg

    async def update(self, cfg: DepartmentDockerRegistry, **kwargs) -> DepartmentDockerRegistry:
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        await self._db.flush()
        return cfg
