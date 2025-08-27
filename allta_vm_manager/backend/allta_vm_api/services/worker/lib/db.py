import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from utils.config import settings
from contextlib import asynccontextmanager


ASYNC_DATABASE_URL = settings.ASYNC_DATABASE_URL
_engine = create_async_engine(ASYNC_DATABASE_URL, pool_pre_ping=True)
_Session = async_sessionmaker(_engine, expire_on_commit=False)

@asynccontextmanager
async def db_session() -> AsyncSession:
    async with _Session() as s:
        yield s