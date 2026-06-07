"""Fixtures для unit-тестов с реальным Postgres.

Top-level `tests/conftest.py` мокает engine только для health-эндпоинта.
Здесь поднимаем настоящую сессию против test-postgres'а (см.
`tests/docker-compose.test.yml`). Схема создаётся один раз на сессию через
alembic, каждый тест работает в SAVEPOINT'е и откатывается.
"""

from __future__ import annotations

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session


TEST_DATABASE_URL_SYNC = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://app_user:app_password@test-postgres:5432/secret_db_test",
        ),
    ),
)


_engine = create_engine(TEST_DATABASE_URL_SYNC, pool_pre_ping=True)


def _reset_schema() -> None:
    """Чистая public-схема и снос enum'ов (alembic пересоздаст)."""
    with _engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("DROP TYPE IF EXISTS credential_scope"))
        conn.execute(text("DROP TYPE IF EXISTS credential_status"))
        conn.commit()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL_SYNC,
        "PYTHONPATH": ".",
    }
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """One-time schema setup per pytest session."""
    _reset_schema()
    _run_migrations()
    yield


@pytest.fixture()
def db() -> Session:
    """Sync SQLAlchemy session с SAVEPOINT-rollback'ом на teardown."""
    conn = _engine.connect()
    conn.begin()
    conn.begin_nested()
    session = Session(bind=conn, expire_on_commit=False)

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session
    session.close()
    conn.rollback()
    conn.close()


# ── Async session для тестов репозиториев/сервисов ──────────────────────────


# psycopg3 — единый dialect `postgresql+psycopg`, async поддерживается
# тем же URL'ом, что и sync (create_async_engine выбирает async-driver
# автоматически).
_async_engine = create_async_engine(TEST_DATABASE_URL_SYNC, pool_pre_ping=True)


@pytest_asyncio.fixture()
async def adb() -> AsyncSession:
    """AsyncSession с SAVEPOINT-rollback'ом — для async-repo/service-тестов.

    Симметрия с sync `db`-fixture: внешняя транзакция держит SAVEPOINT,
    inner-commit'ы внутри сервиса перезапускают savepoint, teardown
    откатывает всё.
    """
    conn = await _async_engine.connect()
    await conn.begin()
    await conn.begin_nested()
    session = AsyncSession(bind=conn, expire_on_commit=False)

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session
    await session.close()
    await conn.rollback()
    await conn.close()
