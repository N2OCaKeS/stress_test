"""Фикстуры для кросс-сервисных интеграционных тестов (auth ↔ loging).

Docker-режим (CI):
  Оба сервиса запускаются через docker-compose.test.yml.
  Переменные AUTH_SERVICE_URL и LOGGING_SERVICE_URL задаются автоматически.

Локальный режим (devcontainer):
  Конфтест авто-запускает оба сервиса как подпроцессы на тестовых БД
  внутри общего dev postgres-кластера. Порты :8010/:8011 (чтобы не
  конфликтовать с dev-стеком на :8000/:8001).

Клиенты:
  auth_client            — для операций с auth_service (без авторизации или с Bearer JWT)
  logging_client         — для admin-операций loging_service (JWT loging_admin)
  logging_service_client — для service-to-service операций loging_service (SERVICE_API_KEY)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

AUTH_URL = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8010")
LOGGING_URL = os.environ.get("LOGGING_SERVICE_URL", "http://localhost:8011")
LOGGING_API_KEY = os.environ.get("LOGGING_SERVICE_API_KEY", "test-logging-api-key")
ADMIN_USERNAME = os.environ.get("E2E_ADMIN_USERNAME", "e2e_admin")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "E2eAdmin1234!")

LOGING_ADMIN_USERNAME = "loging_admin_test"
LOGING_ADMIN_PASSWORD = "LogAdmin1234!"

_AUTH_TEST_DB = os.environ.get(
    "AUTH_TEST_DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@postgres:5432/test_auth",
)
_LOGGING_TEST_DB = os.environ.get(
    "LOGGING_TEST_DATABASE_URL",
    "postgresql+psycopg://app_user:app_password@postgres:5432/test_logging_integ",
)

_LOCAL_MODE = "AUTH_SERVICE_URL" not in os.environ


def _wait_healthy(url: str, path: str, retries: int = 40, delay: float = 1.0) -> None:
    for _ in range(retries):
        try:
            r = httpx.get(f"{url}{path}", timeout=3)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Service at {url}{path} did not become healthy")


def _reset_schema(db_url: str) -> None:
    from sqlalchemy import create_engine, text
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _run_migrations(service_dir: Path, db_url: str) -> None:
    _reset_schema(db_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env={**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(service_dir)},
        check=True,
        capture_output=True,
    )


def _seed_admin(service_dir: Path, db_url: str) -> None:
    subprocess.run(
        [sys.executable, "src/scripts/seed_e2e.py"],
        cwd=service_dir,
        env={
            **os.environ,
            "DATABASE_URL": db_url,
            "PYTHONPATH": str(service_dir),
            "SECRET_KEY": "test-secret-key-for-integration-must-be-32-chars!",
            "ACCESS_TOKEN_TTL_MINUTES": "10",
            "REFRESH_TOKEN_TTL_DAYS": "14",
            "E2E_ADMIN_USERNAME": ADMIN_USERNAME,
            "E2E_ADMIN_PASSWORD": ADMIN_PASSWORD,
        },
        check=True,
        capture_output=True,
    )


def _ensure_db(db_url: str) -> None:
    from urllib.parse import urlparse
    from sqlalchemy import create_engine, text
    parsed = urlparse(db_url)
    db_name = parsed.path.lstrip("/")
    admin_url = db_url.replace(f"/{db_name}", "/postgres")
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


@pytest.fixture(scope="session", autouse=True)
def services(tmp_path_factory):
    if not _LOCAL_MODE:
        _wait_healthy(AUTH_URL, "/api/auth/v1/health")
        _wait_healthy(LOGGING_URL, "/api/logging/v1/health")
        yield
        return

    auth_dir = REPO_ROOT / "auth_service"
    logging_dir = REPO_ROOT / "loging_service"

    for db_url in (_AUTH_TEST_DB, _LOGGING_TEST_DB):
        _ensure_db(db_url)

    _run_migrations(auth_dir, _AUTH_TEST_DB)
    _run_migrations(logging_dir, _LOGGING_TEST_DB)
    _seed_admin(auth_dir, _AUTH_TEST_DB)

    logging_env = {
        **os.environ,
        "DATABASE_URL": _LOGGING_TEST_DB,
        "SERVICE_API_KEY": LOGGING_API_KEY,
        "AUTH_SERVICE_URL": f"http://localhost:8010",
        "PYTHONPATH": str(logging_dir),
        "APP_LOG_LEVEL": "WARNING",
    }
    auth_env = {
        **os.environ,
        "DATABASE_URL": _AUTH_TEST_DB,
        "SECRET_KEY": "test-secret-key-for-integration-must-be-32-chars!",
        "ACCESS_TOKEN_TTL_MINUTES": "10",
        "REFRESH_TOKEN_TTL_DAYS": "14",
        "LOGGING_SERVICE_URL": LOGGING_URL,
        "LOGGING_SERVICE_API_KEY": LOGGING_API_KEY,
        "PYTHONPATH": str(auth_dir),
    }

    # Start loging_service first
    log_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8011"],
        cwd=logging_dir,
        env=logging_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_healthy(LOGGING_URL, "/api/logging/v1/health")

    # Then auth_service
    auth_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8010"],
        cwd=auth_dir,
        env=auth_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_healthy(AUTH_URL, "/api/auth/v1/health")

    yield

    auth_proc.terminate()
    log_proc.terminate()
    auth_proc.wait(timeout=10)
    log_proc.wait(timeout=10)


@pytest.fixture(scope="session")
def auth_client() -> httpx.Client:
    with httpx.Client(base_url=AUTH_URL, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def logging_service_client() -> httpx.Client:
    """Service-to-service client (SERVICE_API_KEY). Use for POST /events, POST /services/{svc}/events."""
    with httpx.Client(
        base_url=LOGGING_URL,
        headers={"Authorization": f"Bearer {LOGGING_API_KEY}"},
        timeout=10,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def admin_token(auth_client: httpx.Client) -> str:
    r = auth_client.post(
        "/api/auth/v1/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def loging_admin_token(auth_client: httpx.Client, admin_token: str) -> str:
    """JWT for a user with platform_role=loging_admin."""
    # Create the user (idempotent — if already exists, login directly)
    r = auth_client.post(
        "/api/auth/v1/users",
        json={
            "username": LOGING_ADMIN_USERNAME,
            "password": LOGING_ADMIN_PASSWORD,
            "platform_role": "loging_admin",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code in (201, 409), f"Failed to create loging_admin user: {r.text}"

    r = auth_client.post(
        "/api/auth/v1/login",
        json={"username": LOGING_ADMIN_USERNAME, "password": LOGING_ADMIN_PASSWORD},
    )
    assert r.status_code == 200, f"loging_admin login failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def logging_client(loging_admin_token: str) -> httpx.Client:
    """Admin client for loging_service (loging_admin JWT). Use for GET /events, /rules, /services."""
    with httpx.Client(
        base_url=LOGGING_URL,
        headers={"Authorization": f"Bearer {loging_admin_token}"},
        timeout=10,
    ) as client:
        yield client


def wait_for_event(
    logging_client: httpx.Client,
    *,
    action: str,
    status: str | None = None,
    severity: str | None = None,
    from_time=None,
    retries: int = 15,
    delay: float = 0.5,
) -> dict:
    """Poll loging_service until a matching event appears (audit is fire-and-forget)."""
    params: dict = {"action": action, "limit": 20}
    if severity:
        params["severity"] = severity
    if from_time:
        params["from_time"] = from_time.isoformat()
    for _ in range(retries):
        r = logging_client.get("/api/logging/v1/events", params=params)
        assert r.status_code == 200, f"GET /events failed: {r.text}"
        for item in r.json()["items"]:
            if status is None or item["status"] == status:
                return item
        time.sleep(delay)
    raise AssertionError(
        f"Event action={action!r} status={status!r} not found after {retries} retries"
    )
