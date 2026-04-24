"""Общие фикстуры для тестов auth_service.

Каждый тест выполняется внутри SAVEPOINT, который откатывается при teardown —
БД всегда чистая без пересоздания схемы.

База данных:
  Docker-compose тестовый стек  → переменная TEST_DATABASE_URL (сервис test-postgres)
  Локальный devcontainer        → postgres:5432/test_auth_unit (создаётся автоматически)

Доступные роли пользователей в тестах:
  account_admin     — платформенный администратор, без отдела
  dept_admin_a      — department_admin в dept_a
  user_a            — обычный пользователь в dept_a (с ролями на service_x)
  dept_admin_b      — department_admin в dept_b
  user_b            — обычный пользователь в dept_b
"""

import os
import subprocess

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# ── URL базы данных ───────────────────────────────────────────────────────────
# Приоритет: TEST_DATABASE_URL (устанавливается docker-compose тестовым стеком).
# Запасной вариант: общий dev postgres для локальных запусков в devcontainer.

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@postgres:5432/test_auth",
)

# Переменные окружения нужно задать до импортов src.* — иначе pydantic-settings
# прочитает настройки до того, как мы подменим DATABASE_URL.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-at-least-32-characters-long")
os.environ.setdefault("ACCESS_TOKEN_TTL_MINUTES", "10")
os.environ.setdefault("REFRESH_TOKEN_TTL_DAYS", "14")

# ── Импорты приложения (после установки переменных окружения) ────────────────
from src.core.security import hash_password  # noqa: E402
from src.db.base import Base  # noqa: E402
from src.dependencies.db import get_db  # noqa: E402
from src.main import create_application  # noqa: E402
from src.models import (  # noqa: E402
    Ban, BotAccount, BotToken, Department, DepartmentServiceAccess,
    OAuthAuthorizationCode, OAuthClient,
    PersonalAccessToken, PlatformService, ServiceRoleDefinition, Session, User, UserServiceRole,
)
from src.models.department_docker_registry import DepartmentDockerRegistry  # noqa: E402
from src.utils.ids import _new_id, service_role_def_id  # noqa: E402

# ── Движки SQLAlchemy ─────────────────────────────────────────────────────────
# Синхронный — только для миграций (Alembic).
_sync_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)

# Асинхронный — для всех фикстур уровня теста (SAVEPOINT-откат).
_async_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


# ── Жизненный цикл схемы ─────────────────────────────────────────────────────

def _ensure_test_db_exists() -> None:
    """Create the test database inside the dev cluster if it doesn't exist yet."""
    from urllib.parse import urlparse
    parsed = urlparse(TEST_DATABASE_URL)
    db_name = parsed.path.lstrip("/")
    # Connect to the always-present 'postgres' maintenance database
    admin_url = TEST_DATABASE_URL.replace(f"/{db_name}", "/postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


def _reset_schema() -> None:
    """Nuke the public schema and recreate it — cleanest way to reset a test DB."""
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(__file__))  # auth_service/
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Ensure test DB exists, wipe schema, run migrations fresh."""
    _ensure_test_db_exists()
    _reset_schema()
    _run_migrations()
    yield
    _reset_schema()   # clean teardown so next session starts from scratch


# ── Откат транзакции после каждого теста ─────────────────────────────────────

@pytest_asyncio.fixture()
async def db():
    """
    AsyncSession привязана к соединению с внешней транзакцией + SAVEPOINT.

    Вызовы session.commit() внутри сервиса освобождают текущий SAVEPOINT и
    автоматически создают новый. Внешняя транзакция откатывается при teardown —
    ничего не сохраняется в тестовой БД.
    """
    conn = await _async_engine.connect()
    await conn.begin()           # внешняя транзакция — никогда не коммитится
    await conn.begin_nested()   # начальный SAVEPOINT

    session = AsyncSession(bind=conn, expire_on_commit=False)

    # После каждого "commit" внутри сессии перезапускаем SAVEPOINT, чтобы
    # следующая операция имела изолированный контекст для отката.
    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session

    await session.close()
    await conn.rollback()
    await conn.close()


# ── FastAPI AsyncClient (HTTP-клиент для тестов) ─────────────────────────────

@pytest_asyncio.fixture()
async def client(db):
    app = create_application()

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ── Вспомогательные функции для создания тестовых данных ─────────────────────

async def _make_user(db, username, password, department_id=None, platform_role=None, status="active"):
    user = User(
        id=_new_id("usr_"),
        username=username,
        password_hash=hash_password(password),
        department_id=department_id,
        platform_role=platform_role,
        status=status,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_dept(db, name):
    dept = Department(id=_new_id("dep_"), name=name, display_name=name.title(), is_active=True)
    db.add(dept)
    await db.flush()
    return dept


_DEFAULT_SERVICE_ROLES = ["admin", "operator", "reader", "guest"]


async def _make_service(db, name):
    svc = PlatformService(service_name=name, display_name=name.title(), is_active=True)
    db.add(svc)
    await db.flush()
    for role_name in _DEFAULT_SERVICE_ROLES:
        db.add(ServiceRoleDefinition(
            id=service_role_def_id(),
            service_name=name,
            role_name=role_name,
            display_name=role_name.title(),
            is_active=True,
        ))
    await db.flush()
    return svc


async def _make_role_def(db, service_name, role_name, display_name=None):
    obj = ServiceRoleDefinition(
        id=service_role_def_id(),
        service_name=service_name,
        role_name=role_name,
        display_name=display_name or role_name.title(),
        is_active=True,
    )
    db.add(obj)
    await db.flush()
    return obj


async def _grant_service(db, dept_id, service_name):
    access = DepartmentServiceAccess(
        id=_new_id("dsa_"), department_id=dept_id,
        service_name=service_name, is_active=True,
    )
    db.add(access)
    await db.flush()
    return access


async def _assign_role(db, user_id, service_name, role):
    r = UserServiceRole(
        id=_new_id("usr_"), user_id=user_id,
        service_name=service_name, role=role, is_active=True,
    )
    db.add(r)
    await db.flush()
    return r


async def _login(client, username, password):
    resp = await client.post("/api/auth/v1/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


# ── Фикстуры: отделы и сервисы ───────────────────────────────────────────────

@pytest_asyncio.fixture()
async def account_admin(db):
    return await _make_user(db, "t_admin", "Admin1234!", platform_role="account_admin")


@pytest_asyncio.fixture()
async def dept_a(db):
    return await _make_dept(db, "dept_alpha")


@pytest_asyncio.fixture()
async def dept_b(db):
    return await _make_dept(db, "dept_beta")


@pytest_asyncio.fixture()
async def service_x(db):
    return await _make_service(db, "service_x")


@pytest_asyncio.fixture()
async def dept_a_with_service(db, dept_a, service_x):
    await _grant_service(db, dept_a.id, service_x.service_name)
    return dept_a


# ── Фикстуры: пользователи ───────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def dept_admin_a(db, dept_a):
    return await _make_user(db, "t_dept_admin_a", "Admin1234!",
                            department_id=dept_a.id, platform_role="department_admin")


@pytest_asyncio.fixture()
async def user_a(db, dept_a_with_service, service_x):
    u = await _make_user(db, "t_user_a", "User1234!", department_id=dept_a_with_service.id)
    await _assign_role(db, u.id, service_x.service_name, "reader")
    return u


@pytest_asyncio.fixture()
async def dept_admin_b(db, dept_b):
    return await _make_user(db, "t_dept_admin_b", "Admin1234!",
                            department_id=dept_b.id, platform_role="department_admin")


@pytest_asyncio.fixture()
async def user_b(db, dept_b):
    return await _make_user(db, "t_user_b", "User1234!", department_id=dept_b.id)


# ── Фикстуры: токены ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def admin_token(client, account_admin):
    return await _login(client, "t_admin", "Admin1234!")


@pytest_asyncio.fixture()
async def dept_admin_a_token(client, dept_admin_a):
    return await _login(client, "t_dept_admin_a", "Admin1234!")


@pytest_asyncio.fixture()
async def user_a_token(client, user_a):
    return await _login(client, "t_user_a", "User1234!")


@pytest_asyncio.fixture()
async def dept_admin_b_token(client, dept_admin_b):
    return await _login(client, "t_dept_admin_b", "Admin1234!")


@pytest_asyncio.fixture()
async def user_b_token(client, user_b):
    return await _login(client, "t_user_b", "User1234!")


# ── Фикстуры: Docker registry ────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def docker_registry_enabled(db, dept_a):
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[],
        push_user_ids=[],
    )
    db.add(cfg)
    await db.flush()
    return cfg
