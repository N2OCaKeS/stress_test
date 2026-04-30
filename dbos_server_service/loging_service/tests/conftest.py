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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.db.base import Base
from src.dependencies.auth import require_admin
from src.dependencies.db import get_db
from src.main import app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@postgres:5432/test_logging",
)

TEST_API_KEY = "test-service-api-key"

ADMIN_IDENTITY = {
    "user_id": "usr_test_admin",
    "username": "test_admin",
    "platform_role": "loging_admin",
    "department_id": None,
    "allowed_services": [],
    "service_roles": {},
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


@pytest.fixture()
def db(TestSessionLocal) -> Session:
    """Session с чистыми таблицами перед каждым тестом."""
    from src.services import rule_service
    session = TestSessionLocal()
    session.execute(text(
        "TRUNCATE TABLE audit_events, audit_rules, service_events RESTART IDENTITY CASCADE"
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
    """TestClient c аутентификацией по SERVICE_API_KEY (для сервисов)."""
    monkeypatch.setenv("SERVICE_API_KEY", TEST_API_KEY)
    # Установить фиктивный URL — это нужно чтобы require_admin доходил до httpx.get,
    # а не возвращал 503 AUTH_SERVICE_NOT_CONFIGURED ещё до вызова.
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
    from src.core.config import get_settings
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _db_override(db)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def admin_client(db, monkeypatch):
    """TestClient с admin identity override (имитирует loging_admin JWT без реального auth_service)."""
    monkeypatch.setenv("SERVICE_API_KEY", TEST_API_KEY)
    from src.core.config import get_settings
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _db_override(db)
    app.dependency_overrides[require_admin] = lambda: ADMIN_IDENTITY

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
