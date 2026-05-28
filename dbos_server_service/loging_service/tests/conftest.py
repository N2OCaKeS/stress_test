"""Конфигурация и фикстуры для тестов loging_service.

База данных:
  Docker-compose тестовый стек  → переменная TEST_DATABASE_URL
  Локальный devcontainer        → postgres:5432/test_logging (создаётся автоматически)

Изоляция тестов: перед каждым тестом все таблицы очищаются через TRUNCATE.

Клиенты:
  client       — service-token auth (SERVICE_API_KEY). Для POST /events, POST /services/{svc}/events.
  admin_client — admin identity override. Для GET /events, /rules/*, GET /services/*, /services/{svc}/events.
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@postgres:5432/test_logging",
)

TEST_API_KEY = "test-service-api-key"

# `RETENTION_LOOP_ENABLED=false` ОБЯЗАН быть выставлен ДО первого
# `from src.*` import'а: `src.main.create_application()` снимает
# `get_settings()` snapshot и проверяет флаг в lifespan'е. Без override
# retention daemon тикает на каждом TestClient'е, бьётся в module-level
# `SessionLocal` (binds to default `DATABASE_URL=localhost:5432`, не
# TEST_DATABASE_URL) и спамит `Retention cleanup failed: connection
# refused` на каждом setup'е под MSK hour=0.
#
# DATABASE_URL отдельно НЕ подменяем — audit middleware self-audit-вызовы
# тоже бьются в localhost:5432, но тесты вокруг audit-эмита намеренно
# инспектируют SessionLocal через monkeypatch, и подмена DATABASE_URL
# ломала бы их предположения о fixture-сегрегации БД.
os.environ.setdefault("RETENTION_LOOP_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.db.base import Base
from src.dependencies.auth import require_admin, require_reader
from src.dependencies.db import get_db
from src.main import app

ADMIN_IDENTITY = {
    "user_id": "usr_test_admin",
    "username": "test_admin",
    "platform_role": "loging_admin",
    "department_id": None,
    "allowed_services": [],
    "service_roles": {},
    # loging_admin не привязан к отделу — видит все события
    "_dept_scope": None,
    "_loging_service_roles": [],
}


# ── Database bootstrap ────────────────────────────────────────────────────────

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


def _reset_schema(db_url: str) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_engine():
    _ensure_test_db_exists()
    _reset_schema(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    _reset_schema(TEST_DATABASE_URL)
    engine.dispose()


@pytest.fixture(scope="session")
def TestSessionLocal(test_engine):
    return sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Сбрасывает per-IP rate-limit между тестами.

    Все тесты идут с одного `127.0.0.1` через ASGITransport — без reset'а
    счётчики slowapi накапливались бы между кейсами, и при достаточном числе
    ingest-вызовов до теста возникали бы случайные 429. Явные проверки лимита —
    в `tests/test_rate_limit.py`.
    """
    from src.main import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def db(TestSessionLocal) -> Session:
    """Session с чистыми таблицами перед каждым тестом."""
    from src.services import rule_service
    session = TestSessionLocal()
    session.execute(text(
        "TRUNCATE TABLE audit_events, audit_rules, service_events, retention_policies RESTART IDENTITY CASCADE"
    ))
    session.commit()
    rule_service.invalidate_cache()
    try:
        yield session
    finally:
        session.close()


# ── DB override helper ────────────────────────────────────────────────────────

def _db_override(db: Session):
    def override():
        try:
            yield db
        finally:
            pass
    return override


# ── Client fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(db, monkeypatch):
    """TestClient c аутентификацией по SERVICE_API_KEY (для сервисов).

    Forces the pooled ``_introspect_client`` to ``None`` after lifespan
    startup so existing tests that ``patch("src.dependencies.auth.httpx.post")``
    still intercept the introspect call via the per-call fallback path. The
    dedicated pool-behaviour tests in ``tests/dependencies/test_auth_pool.py``
    install their own pooled ``AsyncClient`` with ``MockTransport``.
    """
    monkeypatch.setenv("SERVICE_API_KEY", TEST_API_KEY)
    # Установить фиктивный URL — это нужно чтобы require_admin доходил до httpx.get,
    # а не возвращал 503 AUTH_SERVICE_NOT_CONFIGURED ещё до вызова.
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
    from src.core.config import get_settings
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _db_override(db)
    with TestClient(app) as c:
        # After lifespan startup, the pooled AsyncClient is initialised with
        # base_url=http://auth-test:8000. Tests in this suite mock the
        # FALLBACK path (sync httpx.post) — force the pool back to None so
        # those mocks are honoured. The pooled path is exercised explicitly
        # by ``tests/dependencies/test_auth_pool.py``.
        from src.dependencies import auth as _auth_deps
        _pool = _auth_deps._introspect_client
        _auth_deps._introspect_client = None
        # Аналогично pool'у /token: тесты в test_admin_auth патчат
        # `src.api.v1.endpoints.auth.httpx.post`, ожидая fallback-путь.
        _token_pool = _auth_deps._token_proxy_client
        _auth_deps._token_proxy_client = None
        try:
            yield c
        finally:
            # Restore so lifespan-shutdown can close the original client.
            _auth_deps._introspect_client = _pool
            _auth_deps._token_proxy_client = _token_pool
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def admin_client(db, monkeypatch):
    """TestClient с admin identity override (имитирует loging_admin JWT без реального auth_service)."""
    monkeypatch.setenv("SERVICE_API_KEY", TEST_API_KEY)
    from src.core.config import get_settings
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _db_override(db)
    # Админ имеет и admin, и reader-доступ — переопределяем обе зависимости.
    app.dependency_overrides[require_admin] = lambda: ADMIN_IDENTITY
    app.dependency_overrides[require_reader] = lambda: ADMIN_IDENTITY

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def auth_headers() -> dict:
    """Bearer headers для SERVICE_API_KEY (для POST /events, POST /services/{svc}/events)."""
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


# ── Payload factories ─────────────────────────────────────────────────────────

def make_event(**kwargs) -> dict:
    base = {
        "timestamp": "2026-04-19T10:00:00Z",
        "service": "auth_service",
        "action": "user.login",
        "actor_id": "usr_abc123",
        "actor_type": "user",
        "department_id": "dep_nt",
        "status": "success",
        "allowed": True,
        "severity": "INFO",
    }
    base.update(kwargs)
    return base


def make_rule(**kwargs) -> dict:
    base = {
        "name": "test-rule",
        "effect": "SUPPRESS",
        "priority": 100,
    }
    base.update(kwargs)
    return base


def make_event_def(**kwargs) -> dict:
    base = {
        "action": "user.login",
        "description": "User authentication attempt",
        "default_severity": "INFO",
    }
    base.update(kwargs)
    return base
