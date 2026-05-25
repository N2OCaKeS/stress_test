"""Pytest fixtures for server_worker tests.

Сессионная задача: поднять чистую БД с миграциями для tasks-таблицы, чтобы
`_runner.run_task` мог реально читать/писать. Внутри теста используется
`AsyncSessionLocal`, который уже привязан к `src.db.session.engine` — мы не
переопределяем его, а просто гарантируем, что подключённая БД существует
и проходит alembic upgrade head.
"""

from __future__ import annotations

import os
import subprocess
import uuid

# Settings is strict — populate required env before any `src.*` import.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@postgres:5432/server_worker_db_test",
    ),
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SERVER_SERVICE_URL", "http://not-used")
os.environ.setdefault("AUTH_SERVICE_URL", "http://not-used")
os.environ.setdefault("LOGGING_SERVICE_URL", "http://not-used")
os.environ.setdefault("WORKER_BOT_TOKEN", "dummy-test-token")

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text


_TEST_DB_URL = os.environ["DATABASE_URL"]


def _ensure_db_exists() -> None:
    from urllib.parse import urlparse
    parsed = urlparse(_TEST_DB_URL)
    db_name = parsed.path.lstrip("/")
    admin_url = _TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    eng.dispose()


def _reset_schema() -> None:
    eng = create_engine(_TEST_DB_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(__file__))
    env = {**os.environ, "DATABASE_URL": _TEST_DB_URL, "PYTHONPATH": "."}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=service_dir, env=env, check=True, capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    """One-time: ensure DB exists, wipe, migrate."""
    _ensure_db_exists()
    _reset_schema()
    _run_migrations()
    yield
    _reset_schema()


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tasks():
    """TRUNCATE tasks + audit_outbox + worker_heartbeats перед каждым тестом.

    Также очищает process-local `RUNNING_TASKS` — если тест запустил
    `_runner.run_task` и упал между add/discard, set мог сохранить
    «фантомные» task_id, что отравило бы graceful-shutdown-тесты.

    `worker_heartbeats` тоже truncate'аем — sweep-тесты опираются на
    «нет активных воркеров» как orphan-условие; stale row из предыдущего
    теста ломал бы изоляцию.
    """
    from src.db.session import engine
    from src.tasks._runner_state import reset_for_tests
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE tasks CASCADE"))
        await conn.execute(text("TRUNCATE audit_outbox RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE worker_heartbeats CASCADE"))
    reset_for_tests()
    yield
    reset_for_tests()


@pytest_asyncio.fixture
async def make_task():
    """Создаёт Task row в БД и возвращает его id."""
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo

    async def _factory(
        *,
        task_kind: str = "power.on",
        target_server_id: str | None = "srv_test",
        payload: dict | None = None,
        created_by: str | None = "usr_caller",
        request_id: str | None = "req_test",
    ) -> str:
        tid = f"tsk_{uuid.uuid4().hex[:16]}"
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
                "payload": payload or {"server_id": target_server_id},
                "created_by": created_by,
                "request_id": request_id,
            })
            await session.commit()
        return tid

    return _factory


@pytest_asyncio.fixture
async def fetch_task():
    """Возвращает Task row по id."""
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo

    async def _get(task_id: str):
        async with AsyncSessionLocal() as session:
            return await task_repo.get_by_id(session, task_id)

    return _get


@pytest.fixture
def captured_audit(monkeypatch):
    """In-memory сборщик audit-событий.

    После outbox-рефакторинга `_runner.run_task` больше не вызывает
    `audit_client.emit` напрямую — он INSERT'ит в `audit_outbox` и зовёт
    `audit_outbox_publisher.flush_outbox()`. Publisher уже вызывает
    `audit_client.emit`. Поэтому monkeypatch'им именно его — фикстура
    остаётся совместимой с существующими тестами (они проверяют
    `captured_audit[0]["action"]` после run_task).
    """
    events: list[dict] = []

    async def fake_emit(action, **kw):
        events.append({"action": action, **kw})

    monkeypatch.setattr("src.services.audit_client.emit", fake_emit)
    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.audit_client.emit", fake_emit
    )
    return events
