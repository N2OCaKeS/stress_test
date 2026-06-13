"""DAO для `Department` + `DepartmentServiceAccess` — CRUD отделов и grant/revoke к сервисам."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.department import Department
from src.models.department_service_access import DepartmentServiceAccess
from src.utils.ids import department_id
from src.utils.time import utcnow


class DepartmentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, dept_id: str) -> Department | None:
        return await self._db.get(Department, dept_id)

    async def get_for_update(self, dept_id: str) -> Department | None:
        """`SELECT ... FOR UPDATE` — row-lock на отдел для hard-delete.

        Сериализует параллельные hard-delete'ы и блокирует concurrent
        мутации (`update_department`, grant/revoke service access) до
        commit'а.
        """
        stmt = select(Department).where(Department.id == dept_id).with_for_update()
        return await self._db.scalar(stmt)

    async def get_by_name(self, name: str) -> Department | None:
        return await self._db.scalar(select(Department).where(Department.name == name))

    async def list_all(self) -> list[Department]:
        result = await self._db.scalars(select(Department).where(Department.is_active.is_(True)))
        return list(result)

    async def create(self, name: str) -> Department:
        dept = Department(id=department_id(), name=name)
        self._db.add(dept)
        await self._db.flush()
        return dept

    async def update(
        self,
        dept: Department,
        *,
        name: str | None,
        description: str | None,
    ) -> Department:
        """Точечный апдейт `name` и/или `description`.

        Меняем только те поля, для которых передано не-None значение —
        это позволяет PATCH-семантике отличать «не трогать» от «очистить».
        """
        if name is not None:
            dept.name = name
        if description is not None:
            dept.description = description
        await self._db.flush()
        await self._db.refresh(dept)
        return dept

    # ── service access ────────────────────────────────────────────────────────

    async def get_access(self, dept_id: str, service_name: str) -> DepartmentServiceAccess | None:
        return await self._db.scalar(
            select(DepartmentServiceAccess).where(
                DepartmentServiceAccess.department_id == dept_id,
                DepartmentServiceAccess.service_name == service_name,
            )
        )

    async def has_active_access(self, dept_id: str, service_name: str) -> bool:
        row = await self.get_access(dept_id, service_name)
        return row is not None and row.is_active

    async def list_active_services(self, dept_id: str) -> list[str]:
        rows = await self._db.scalars(
            select(DepartmentServiceAccess.service_name).where(
                DepartmentServiceAccess.department_id == dept_id,
                DepartmentServiceAccess.is_active.is_(True),
            )
        )
        return list(rows)

    async def grant_access(
        self, dept_id: str, service_name: str, granted_by: str | None
    ) -> DepartmentServiceAccess:
        from src.utils.ids import _new_id
        access = DepartmentServiceAccess(
            id=_new_id("dsa_"),
            department_id=dept_id,
            service_name=service_name,
            is_active=True,
            granted_by=granted_by,
        )
        self._db.add(access)
        await self._db.flush()
        return access

    async def revoke_access(
        self, access: DepartmentServiceAccess, revoked_by: str | None
    ) -> None:
        access.is_active = False
        access.revoked_at = utcnow()
        access.revoked_by = revoked_by
        await self._db.flush()

    async def count_users(self, dept_id: str) -> int:
        """Сколько пользователей привязано к отделу (все, без фильтра по is_active).

        Используется для `user_count` в ответе одиночного отдела. Боты не
        входят в подсчёт — считаются только записи в `users`.
        """
        from sqlalchemy import func
        from src.models.user import User
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.department_id == dept_id)
        )
        return await self._db.scalar(stmt) or 0

    async def user_counts_by_department(self) -> dict[str, int]:
        """Один агрегатный `GROUP BY department_id` — число юзеров на отдел.

        Возвращает map `department_id -> count` по всем пользователям с
        непустым `department_id` (без фильтра по is_active, без ботов).
        Отделы без юзеров в map не попадают — caller подставляет 0.
        """
        from sqlalchemy import func
        from src.models.user import User
        stmt = (
            select(User.department_id, func.count())
            .where(User.department_id.is_not(None))
            .group_by(User.department_id)
        )
        rows = await self._db.execute(stmt)
        return {dept_id: count for dept_id, count in rows.all()}

    async def count_active_users(self, dept_id: str) -> int:
        """Сколько активных юзеров живёт в отделе.

        Hard-delete guard'у нужно отличить «отдел пустой» от «там кто-то
        ещё есть» — забаненных/disabled не считаем, они уже не пользуются
        платформой и не блокируют выпиливание отдела (если так решит admin).
        """
        from sqlalchemy import func
        from src.models.user import User
        stmt = (
            select(func.count())
            .select_from(User)
            .where(
                User.department_id == dept_id,
                User.is_active.is_(True),
            )
        )
        return await self._db.scalar(stmt) or 0

    async def delete(self, dept: Department) -> None:
        """Hard-delete отдела. CASCADE'ятся `DepartmentServiceAccess`,
        `ServiceRoleDefinition`, `UserGroup`, `DepartmentDockerRegistry`
        через ondelete='CASCADE' на FK. Bots и oauth_clients имеют
        ondelete='RESTRICT' — за их зачистку отвечает caller (service-уровень).
        """
        await self._db.delete(dept)
        await self._db.flush()
