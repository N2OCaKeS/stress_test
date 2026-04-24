"""User repository."""

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

    async def list_by_department(self, department_id: str) -> list[User]:
        result = await self._db.scalars(select(User).where(User.department_id == department_id))
        return list(result)

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

    async def increment_failed_attempts(self, user: User) -> None:
        user.failed_login_attempts += 1
        await self._db.flush()

    async def reset_failed_attempts(self, user: User) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        await self._db.flush()

    async def exists_username(self, username: str) -> bool:
        return await self._db.scalar(
            select(User.id).where(User.username == username)
        ) is not None

    async def count(self) -> int:
        return await self._db.scalar(select(func.count()).select_from(User)) or 0
