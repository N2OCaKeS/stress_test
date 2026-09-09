"""Pytest fixtures для testing_service.

Реальный Postgres+Redis из `tests/docker-compose.test.yml` (не моки —
health/ready должны реально пингануть обе зависимости). Схема поднимается
alembic'ом один раз на сессию, чтобы сиды миграций были на месте.
`_introspect` подменяется, чтобы тесты не ходили в auth_service.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

# Дефолты, чтобы Settings проходил валидаторы, если тест запущен вне
# docker-compose.test.yml (там эти же значения приходят через environment).
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@localhost:5432/testing_db_test",
    ),
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("AUTH_SERVICE_URL", "http://auth-not-used-in-tests")
os.environ.setdefault("APP_ENV", "test")

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


# ── DB lifecycle ─────────────────────────────────────────────────────────────

def _ensure_test_db_exists() -> None:
    from urllib.parse import urlparse

    parsed = urlparse(TEST_DATABASE_URL)
    db_name = parsed.path.lstrip("/")
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
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(__file__))
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
    """Один раз на сессию: чистая схема + миграции (вместе с сидами)."""
    _ensure_test_db_exists()
    _reset_schema()
    _run_migrations()
    yield
    _reset_schema()


@pytest.fixture(autouse=True)
def _cleanup_created_variables():
    """Снести переменные, тесты, стенды и очередь, созданные тестом.

    У сидированных миграцией `created_by` пуст, у заведённых через API — всегда
    заполнен id актора, так что этого признака достаточно, чтобы отличить одни
    от других. `queue_items` удаляются первыми — `stand_id`/`test_id`/
    `retry_of_id` держат `ON DELETE RESTRICT`/`SET NULL` FK на `test_stands`/
    `test_definitions`/самих себя, поэтому пока есть хоть одна строка очереди,
    снести стенд/тест нельзя. `test_runs` — `SET NULL` от `queue_items`, порядок
    относительно них неважен. `test_definitions` удаляются следующими — ON
    DELETE CASCADE сносит их `test_command_args` заодно, тогда
    `global_variables` со ссылающимися слотами гарантированно уже свободны от
    FK. `test_stands` ни от кого не зависит — порядок относительно них
    неважен. `department_test_settings` не привязан ни к чему по FK, чистим
    по department_id, начинающемуся с тестового префикса `dep_`.
    """
    yield
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            # test_logs не отмечены created_by (не пользовательская сущность,
            # заводятся лениво internal-эндпоинтами) — таблица целиком в
            # владении этого домена, чистим безусловно. Каскадом сносит
            # test_log_segments/test_log_blobs (ON DELETE CASCADE).
            conn.execute(text("DELETE FROM test_logs"))
            # stp_cells/stp_test_runs — целиком в владении этого домена (нет
            # сида, никогда не сеются миграцией), чистим безусловно, до
            # queue_items/test_stands (FK stp_cells.queue_item_id SET NULL,
            # stp_test_runs.stand_id RESTRICT).
            conn.execute(text("DELETE FROM stp_cells"))
            conn.execute(text("DELETE FROM stp_test_runs"))
            conn.execute(text("DELETE FROM stp_test_cases WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM queue_items WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM test_runs WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM test_definitions WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM global_variables WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM test_stands WHERE created_by IS NOT NULL"))
            conn.execute(text("DELETE FROM department_test_settings WHERE department_id LIKE 'dep\\_%' ESCAPE '\\'"))
            conn.execute(text(
                "DELETE FROM department_integration_settings WHERE department_id LIKE 'dep\\_%' ESCAPE '\\'"
            ))
    finally:
        engine.dispose()


# ── Identity / introspect mock ───────────────────────────────────────────────

_FAKE_INTROSPECT: dict[str, dict] = {}


def register_token(token: str, body: dict) -> None:
    _FAKE_INTROSPECT[token] = body


def auth_hdr(token: str) -> dict[str, str]:
    """Authorization-заголовок для тестового токена."""
    return {"Authorization": f"Bearer {token}"}


def _identity_body(
    *,
    user_id: str,
    username: str = "tester",
    department_id: str | None = None,
    platform_role: str | None = None,
    service_roles: dict[str, list[str]] | None = None,
    allowed_services: list[str] | None = None,
    active: bool = True,
    is_banned: bool = False,
    subject_type: str | None = None,
) -> dict:
    if allowed_services is None:
        if platform_role in {"account_admin", "loging_admin", "loging_reader"}:
            allowed_services = []
        else:
            allowed_services = ["testing_service"]
    return {
        "active": active,
        "sub": user_id,
        "username": username,
        "department_id": department_id,
        "platform_role": platform_role,
        "allowed_services": allowed_services,
        "service_roles": service_roles or {},
        "is_banned": is_banned,
        "subject_type": subject_type,
    }


@pytest.fixture(autouse=True)
def _patch_introspect(monkeypatch):
    """Перехватывает `_introspect` — тесты работают без живого auth_service."""

    async def fake_introspect(token: str) -> dict:
        body = _FAKE_INTROSPECT.get(token)
        if body is None:
            return {"active": False}
        return body

    monkeypatch.setattr("src.dependencies.auth._introspect", fake_introspect)
    _FAKE_INTROSPECT.clear()
    yield
    _FAKE_INTROSPECT.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Сброс per-IP счётчиков slowapi между тестами (все идут с 127.0.0.1)."""
    from src.main import app

    limiter = getattr(app.state, "limiter", None)
    if limiter is not None:
        limiter.reset()
    yield
    if limiter is not None:
        limiter.reset()


# ── Tokens ───────────────────────────────────────────────────────────────────

@pytest.fixture
def make_token():
    """Фабрика тестовых токенов: make_token(service_roles=..., ...) → str."""

    def _factory(
        *,
        platform_role: str | None = None,
        department_id: str | None = None,
        service_roles: dict[str, list[str]] | None = None,
        allowed_services: list[str] | None = None,
        user_id: str | None = None,
        username: str = "tester",
        active: bool = True,
        is_banned: bool = False,
        subject_type: str | None = None,
    ) -> str:
        token = f"dbos_pat_{uuid.uuid4().hex}"
        register_token(token, _identity_body(
            user_id=user_id or f"usr_{uuid.uuid4().hex[:8]}",
            username=username,
            department_id=department_id,
            platform_role=platform_role,
            service_roles=service_roles,
            allowed_services=allowed_services,
            active=active,
            is_banned=is_banned,
            subject_type=subject_type,
        ))
        return token

    return _factory


@pytest.fixture
def dept_a() -> str:
    return "dep_a"


@pytest.fixture
def admin_token(make_token, dept_a) -> str:
    """Пользователь с системной сервисной ролью `admin` в testing_service."""
    return make_token(
        department_id=dept_a,
        service_roles={"testing_service": ["admin"]},
    )


@pytest.fixture
def guest_token(make_token, dept_a) -> str:
    """Пользователь с ролью `guest` — читает каталог, но не пишет."""
    return make_token(
        department_id=dept_a,
        service_roles={"testing_service": ["guest"]},
    )


@pytest.fixture
def no_role_token(make_token, dept_a) -> str:
    """Аутентифицированный пользователь вообще без ролей в сервисе."""
    return make_token(department_id=dept_a)


@pytest_asyncio.fixture
async def client():
    """ASGI client поверх FastAPI app."""
    from src.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac
