"""DAO для `User` — CRUD + lockout-helpers (atomic increment/reset)."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
from src.utils.ids import user_id


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, uid: str) -> User | None:
        return await self._db.get(User, uid)

    async def get_by_username(self, username: str) -> User | None:
        return await self._db.scalar(select(User).where(User.username == username))

    async def get_by_email(self, email: str) -> User | None:
        return await self._db.scalar(select(User).where(User.email == email))

    async def list_all(
        self,
        limit: int | None = None,
        offset: int = 0,
        include_banned: bool = False,
        status_filter: str | None = None,
    ) -> list[User]:
        """Список юзеров.

        `include_banned=False` (default) — поведение UI до фикса: только
        `is_active=True`. `include_banned=True` снимает фильтр `is_active` —
        admin-список видит забаненных/заблокированных.

        `status_filter` (опционально) — точное совпадение `User.status`.
        Применяется на уровне SQL до limit/offset, чтобы пагинация и
        `count_by_status` отдавали согласованные числа.
        """
        stmt = select(User).order_by(User.created_at, User.id)
        if not include_banned:
            stmt = stmt.where(User.is_active.is_(True))
        if status_filter is not None:
            stmt = stmt.where(User.status == status_filter)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._db.scalars(stmt)
        return list(result)

    async def list_by_department(
        self,
        department_id: str,
        limit: int | None = None,
        offset: int = 0,
        include_banned: bool = False,
        status_filter: str | None = None,
    ) -> list[User]:
        stmt = (
            select(User)
            .where(User.department_id == department_id)
            .order_by(User.created_at, User.id)
        )
        if not include_banned:
            stmt = stmt.where(User.is_active.is_(True))
        if status_filter is not None:
            stmt = stmt.where(User.status == status_filter)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._db.scalars(stmt)
        return list(result)

    async def list_by_ids(self, user_ids: list[str]) -> list[User]:
        """Batch-выборка юзеров по списку id. Пустой список — пустой результат."""
        if not user_ids:
            return []
        result = await self._db.scalars(select(User).where(User.id.in_(user_ids)))
        return list(result)

    async def count_active(
        self,
        include_banned: bool = False,
        status_filter: str | None = None,
    ) -> int:
        """Полное число юзеров в скоупе списка.

        `status_filter` фильтрует на SQL — должен соответствовать тому же
        фильтру, что и `list_all`, иначе `total` для `X-Total-Count` разойдётся
        со страницей.
        """
        stmt = select(func.count()).select_from(User)
        if not include_banned:
            stmt = stmt.where(User.is_active.is_(True))
        if status_filter is not None:
            stmt = stmt.where(User.status == status_filter)
        return await self._db.scalar(stmt) or 0

    async def count_by_department(
        self,
        department_id: str,
        include_banned: bool = False,
        status_filter: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.department_id == department_id)
        )
        if not include_banned:
            stmt = stmt.where(User.is_active.is_(True))
        if status_filter is not None:
            stmt = stmt.where(User.status == status_filter)
        return await self._db.scalar(stmt) or 0

    async def create(
        self,
        username: str,
        password_hash: str,
        department_id: str,
        email: str | None = None,
        platform_role: str | None = None,
        created_by: str | None = None,
    ) -> User:
        user = User(
            id=user_id(),
            username=username,
            password_hash=password_hash,
            department_id=department_id,
            email=email,
            platform_role=platform_role,
            created_by=created_by,
        )
        self._db.add(user)
        await self._db.flush()
        return user

    async def update(self, user: User, **kwargs) -> User:
        for key, value in kwargs.items():
            setattr(user, key, value)
        await self._db.flush()
        return user

    async def increment_failed_attempts(self, user: User) -> int:
        """Атомарный инкремент `failed_login_attempts`, возвращает новое значение.

        Через `UPDATE ... RETURNING` — concurrent login'ы не гонкуются на
        stale in-memory счётчике. ORM-инстанс синхронизируется in-place,
        caller'ы могут продолжать использовать `user.failed_login_attempts`.
        """
        stmt = (
            update(User)
            .where(User.id == user.id)
            .values(failed_login_attempts=User.failed_login_attempts + 1)
            .returning(User.failed_login_attempts)
        )
        new_value = await self._db.scalar(stmt)
        if new_value is not None:
            user.failed_login_attempts = new_value
        return new_value if new_value is not None else user.failed_login_attempts

    async def set_locked_until(self, user: User, locked_until: datetime) -> None:
        """Записать `locked_until` через явный UPDATE (без rollback-сюрпризов)."""
        stmt = (
            update(User)
            .where(User.id == user.id)
            .values(locked_until=locked_until)
        )
        await self._db.execute(stmt)
        user.locked_until = locked_until

    async def reset_failed_attempts(self, user: User) -> None:
        """Атомарно очистить `failed_login_attempts` и `locked_until` (после успешного login'а)."""
        stmt = (
            update(User)
            .where(User.id == user.id)
            .values(failed_login_attempts=0, locked_until=None)
        )
        await self._db.execute(stmt)
        user.failed_login_attempts = 0
        user.locked_until = None

    async def exists_username(self, username: str) -> bool:
        return await self._db.scalar(
            select(User.id).where(User.username == username)
        ) is not None

    async def count(self) -> int:
        return await self._db.scalar(select(func.count()).select_from(User)) or 0

    async def count_active_account_admins(self) -> int:
        """Сколько активных account_admin'ов осталось в системе.

        Используется hard-delete guard'ом: запретить снос последнего
        account_admin'а, иначе платформа теряет access к управлению.
        Считаем только `is_active=True` — забаненные/disabled админы
        не способны войти, и формально учитывать их как «защитников»
        нельзя.
        """
        from src.core.constants import PlatformRole
        stmt = (
            select(func.count())
            .select_from(User)
            .where(
                User.platform_role == PlatformRole.ACCOUNT_ADMIN.value,
                User.is_active.is_(True),
            )
        )
        return await self._db.scalar(stmt) or 0

    async def get_for_update(self, uid: str) -> User | None:
        """`SELECT ... FOR UPDATE` — берём row-lock на юзера.

        Нужно hard-delete'у: между «check single-admin» / «cascade revoke» /
        собственно `DELETE` запросами параллельный writer мог бы изменить
        `platform_role`/`status` и сломать инвариант. FOR UPDATE сериализует
        конкурентные hard-delete'ы на одном user_id и держит lock на любого
        кто решит этого юзера апдейтить параллельно.
        """
        stmt = select(User).where(User.id == uid).with_for_update()
        return await self._db.scalar(stmt)

    async def delete(self, user: User) -> None:
        """Hard-delete юзера. Все cascade-relationship'ы (sessions, PAT,
        UserServiceRole, Ban, UserGroupMembership) уходят по
        ``cascade="all, delete-orphan"`` из ORM-модели."""
        await self._db.delete(user)
        await self._db.flush()
