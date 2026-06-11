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
os.environ.setdefault("SERVICE_API_KEY", "pytest-service-api-key-shared-secret")
# identity-cache disabled по умолчанию в тестах. Существующие тесты
# (`test_admin_guard_revalidate.py`) мутируют User-state прямым SQL'ом мимо
# service-level invalidate-хуков, поэтому TTL>0 ломал бы их. Тесты,
# проверяющие сам кэш (`test_p2_identity_ban_cache.py`), поднимают TTL через
# `monkeypatch` внутри теста.
os.environ.setdefault("IDENTITY_CACHE_TTL_SECONDS", "0")

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


def _seed_e2e_admin() -> None:
    """Воссоздать e2e_admin в свежей БД (мы её только что ресетнули).

    auth-service-e2e seed'ит этого юзера при своём старте, но `_reset_schema`
    выше стёр БД, так что повторяем seed для совместимости e2e-тестов.
    Идемпотентно — `seed_e2e.py` пропускает, если юзер уже есть."""
    service_dir = os.path.dirname(os.path.dirname(__file__))
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "PYTHONPATH": ".",
        "E2E_ADMIN_USERNAME": os.environ.get("E2E_ADMIN_USERNAME", "e2e_admin"),
        "E2E_ADMIN_PASSWORD": os.environ.get("E2E_ADMIN_PASSWORD", "E2eAdmin1234!"),
    }
    subprocess.run(
        ["python", "src/scripts/seed_e2e.py"],
        cwd=service_dir, env=env, check=False, capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Ensure test DB exists, wipe schema, run migrations fresh."""
    _ensure_test_db_exists()
    _reset_schema()
    _run_migrations()
    _seed_e2e_admin()
    yield
    _reset_schema()   # clean teardown so next session starts from scratch


# ── Очистка module-level кэшей перед каждым тестом ──────────────────────────
#
# `dependencies.auth._identity_cache` — module-level TTL-кэш на ~5s.
# Между тестами SAVEPOINT откатывает БД, но кэш остаётся: если тест A
# залогинился как user_a и закэшировал identity, тест B с тем же токеном
# (маловероятно, но возможно при `_login()` детерминированных фикстурах)
# получит stale identity из кэша, ссылающуюся на объект из rollback'нутой
# транзакции. Чистим явно перед каждым тестом.


@pytest.fixture(autouse=True)
def _clear_module_level_caches():
    """Очистить identity-кэш перед каждым тестом."""
    try:
        from src.dependencies.auth import _identity_cache_clear
        _identity_cache_clear()
    except ImportError:
        pass
    yield
    try:
        from src.dependencies.auth import _identity_cache_clear
        _identity_cache_clear()
    except ImportError:
        pass


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

class _AuthorizationHeaderInjectingTransport(ASGITransport):
    """ASGI transport that auto-attaches SERVICE_API_KEY bearer header to
    requests targeting /authorization/* endpoints, unless the test already
    set an Authorization header explicitly.

    Rationale: `/introspect` and `/service-access` are protected by
    `require_service_token`. Without this injector every old test would
    need a header rewrite. Tests that want to *probe* the guard (no header
    or wrong header) override Authorization explicitly — that takes
    priority and bypasses the injector.
    """

    async def handle_async_request(self, request):  # type: ignore[override]
        path = request.url.path
        # httpx Headers is case-insensitive; `__contains__` handles that.
        if "/authorization/" in path and "authorization" not in request.headers:
            request.headers["Authorization"] = (
                f"Bearer {os.environ['SERVICE_API_KEY']}"
            )
        return await super().handle_async_request(request)


@pytest_asyncio.fixture()
async def client(db):
    app = create_application()

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=_AuthorizationHeaderInjectingTransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture()
async def raw_client(db):
    """Same app, but WITHOUT the SERVICE_API_KEY injector.

    Use this to probe the `/authorization/*` guard directly (no header,
    wrong header, etc.). All other tests should use ``client``.
    """
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
    dept = Department(id=_new_id("dep_"), name=name, is_active=True)
    db.add(dept)
    await db.flush()
    return dept


_DEFAULT_SERVICE_ROLES = ["operator", "reader", "guest"]


async def _make_service(db, name):
    """Create a platform service. Role definitions are seeded per-department
    when access is granted (see _grant_service)."""
    from sqlalchemy import select

    existing = await db.scalar(select(PlatformService).where(PlatformService.service_name == name))
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            await db.flush()
        return existing
    svc = PlatformService(service_name=name, is_active=True)
    db.add(svc)
    await db.flush()
    return svc


async def _make_role_def(db, department_id, service_name, role_name, is_system=False):
    obj = ServiceRoleDefinition(
        id=service_role_def_id(),
        department_id=department_id,
        service_name=service_name,
        role_name=role_name,
        is_active=True,
        is_system=is_system,
    )
    db.add(obj)
    await db.flush()
    return obj


async def _grant_service(db, dept_id, service_name):
    """Grant a department access to a service and seed the default role catalog
    for that (department, service) pair: `admin` (system) + reader/operator/guest."""
    access = DepartmentServiceAccess(
        id=_new_id("dsa_"), department_id=dept_id,
        service_name=service_name, is_active=True,
    )
    db.add(access)
    await _make_role_def(db, dept_id, service_name, "admin", is_system=True)
    for role_name in _DEFAULT_SERVICE_ROLES:
        await _make_role_def(db, dept_id, service_name, role_name)
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
    return await _make_user(db, "t_admin", "Admin12345678!", platform_role="account_admin")


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


@pytest_asyncio.fixture()
async def docker_registry_service_granted(db, dept_a_with_service):
    """Сервис `docker_registry` (имя совпадает с `DOCKER_REGISTRY_SCOPE`)
    зарегистрирован и выдан dept_a. Нужен, чтобы PAT и bot могли указать
    `allowed_services=["docker_registry"]` (PAT-schema требует min_length=1,
    а scope-guard в `_authenticate_subject` пускает только если этот scope
    есть в `pat.allowed_services` / `bot.allowed_services`)."""
    from src.services.docker_registry_service import DOCKER_REGISTRY_SCOPE
    svc = await _make_service(db, DOCKER_REGISTRY_SCOPE)
    await _grant_service(db, dept_a_with_service.id, svc.service_name)
    return svc


# ── Фикстуры: пользователи ───────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def dept_admin_a(db, dept_a):
    return await _make_user(db, "t_dept_admin_a", "Admin12345678!",
                            department_id=dept_a.id, platform_role="department_admin")


@pytest_asyncio.fixture()
async def user_a(db, dept_a_with_service, service_x):
    u = await _make_user(db, "t_user_a", "User12345678!", department_id=dept_a_with_service.id)
    await _assign_role(db, u.id, service_x.service_name, "reader")
    return u


@pytest_asyncio.fixture()
async def dept_admin_b(db, dept_b):
    return await _make_user(db, "t_dept_admin_b", "Admin12345678!",
                            department_id=dept_b.id, platform_role="department_admin")


@pytest_asyncio.fixture()
async def user_b(db, dept_b):
    return await _make_user(db, "t_user_b", "User12345678!", department_id=dept_b.id)


# ── Фикстуры: токены ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def admin_token(client, account_admin):
    return await _login(client, "t_admin", "Admin12345678!")


@pytest_asyncio.fixture()
async def dept_admin_a_token(client, dept_admin_a):
    return await _login(client, "t_dept_admin_a", "Admin12345678!")


@pytest_asyncio.fixture()
async def user_a_token(client, user_a):
    return await _login(client, "t_user_a", "User12345678!")


@pytest_asyncio.fixture()
async def dept_admin_b_token(client, dept_admin_b):
    return await _login(client, "t_dept_admin_b", "Admin12345678!")


@pytest_asyncio.fixture()
async def user_b_token(client, user_b):
    return await _login(client, "t_user_b", "User12345678!")


# ── Фикстуры: service-to-service auth ────────────────────────────────────────

@pytest.fixture()
def service_auth_headers():
    """Authorization header with SERVICE_API_KEY for /authorization/* endpoints.

    Both /introspect and /service-access are guarded by `require_service_token`
    (shared secret in `SERVICE_API_KEY`). Tests calling those endpoints must
    pass this header.
    """
    return {"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"}


# ── Rate-limit reset (per-route limiter, slowapi) ─────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Сбрасывает per-IP rate-limit между тестами.

    Все тесты идут с одного `127.0.0.1` через ASGITransport — без reset'а
    счётчики slowapi сохранили бы состояние между кейсами и под нагрузкой
    нескольких login-тестов начались бы случайные 429. Лимиты явно проверяются
    только в `tests/middleware/test_rate_limit.py`.
    """
    from src.main import limiter

    limiter.reset()
    yield
    limiter.reset()


# ── Фикстуры: audit-перехват ─────────────────────────────────────────────────

@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """Перехватывает payload, отправляемые `audit_service.emit()`.

    Подменяет sync (`httpx.post`) и async (`httpx.AsyncClient`) пути в
    `src.services.audit_service`, плюс `get_settings` — чтобы
    `logging_service_url`/`api_key` были не-пустыми и emit реально вышел в
    http-канал (где его перехватывает мок). Возвращает list, в который
    кладутся отправленные json-payload'ы.
    """
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers):
            captured.append(json)

            class R:
                status_code = 201

            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type(
            "S",
            (),
            {"logging_service_url": "http://test", "logging_service_api_key": "k"},
        )(),
    )
    return captured


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
