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
# Redis-stash decrypt — общий c server_service'ом ключ. Тесты
# round-trip'ят encrypt/decrypt внутри worker'а; кросс-сервисный round-trip
# покрывается deployment-конфигом (`scripts/k8s/gen_secrets.sh` кладёт один
# REDIS_STASH_ENCRYPTION_KEY в общий Secret).
os.environ.setdefault(
    "REDIS_STASH_ENCRYPTION_KEY",
    "test-redis-stash-encryption-key-do-not-use-anywhere-else",
)
os.environ.setdefault("REDIS_STASH_ENCRYPTION_KEY_VERSION", "1")
os.environ.setdefault("HKDF_SALT_HEX", "deadbeefcafebabe0011223344556677")

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

    Shared circuit breaker для audit-publisher хранит state в Redis с
    единственным набором ключей (канал один, не per-host). Без сброса
    тест A, который заставил emit() падать N раз, оставил бы breaker
    open для теста B, и B увидел бы `CircuitBreakerOpenError` вместо
    своих ожидаемых HTTP-исключений. Сбрасываем best-effort: если
    Redis недоступен — breaker и так fail-open'ит, тесту это не помешает.
    """
    from src.db.session import engine
    from src.services import audit_publisher_breaker, redis_pool
    from src.tasks._runner_state import reset_for_tests
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE tasks CASCADE"))
        await conn.execute(text("TRUNCATE audit_outbox RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE worker_heartbeats CASCADE"))
    # `redis_pool.get_redis()` кеширует `aioredis.Redis` на module-level.
    # Под `asyncio_mode = "auto"` pytest-asyncio даёт каждому тесту свой
    # event loop, а закешированный клиент остаётся привязан к loop'у
    # первого теста, который его создал. Любой `await client.eval(...)`
    # из последующего теста кидает `RuntimeError: got Future attached to
    # a different loop`. Сбрасываем кеш до того, как дёргаем breaker.reset
    # — `audit_publisher_breaker.reset()` лениво поднимет новый клиент
    # уже на текущем loop'е.
    redis_pool.reset_for_tests()
    try:
        await audit_publisher_breaker.reset()
    except Exception:
        pass
    # Settings cache leak guard: некоторые тесты зовут
    # `get_settings.cache_clear()`, после чего `src.main._settings`
    # (модульная переменная) держит уже «отстреленный» инстанс. Если
    # следующий тест патчит `_settings.worker_id` — патч уходит в старый
    # инстанс, а `get_worker_id()` берёт новый из `get_settings()`. Чтобы
    # heartbeat/worker_id тесты переживали этот leak, перепривязываем
    # `_settings` к актуальному lru_cache-результату перед каждым тестом.
    try:
        import src.main as _main_mod
        from src.core.config import get_settings as _gs
        _main_mod._settings = _gs()
    except Exception:
        pass
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


@pytest_asyncio.fixture
async def stash_dispatch_creds():
    """Положить provision-креды в Redis под `dbos:dispatch_creds:<id>` и
    вернуть ключ, который тест может вписать в payload как `creds_stash_key`.

    Имитирует то, что server_service делает перед dispatch'ем: пишет
    plaintext-секреты в Redis с TTL, в payload едет только ссылка. Без
    этого worker фейлится `DISPATCH_STASH_MISSING` ещё до `_account_creds`.
    """
    import json
    import uuid as _uuid

    import redis.asyncio as aioredis

    from src.core.config import get_settings

    written: list[str] = []

    async def _stash(
        password_plaintext: str | None = "sess-pwd",
        ssh_private_key_plaintext: str | None = None,
        suffix: str | None = None,
    ) -> str:
        token = suffix or _uuid.uuid4().hex[:16]
        key = f"dbos:dispatch_creds:{token}"
        settings = get_settings()
        from src.services.redis_stash_crypto import (
            aad_for_redis_stash,
            encrypt_stash,
            stash_id_from_key,
        )

        payload = json.dumps({
            "password_plaintext": password_plaintext,
            "ssh_private_key_plaintext": ssh_private_key_plaintext,
        })
        envelope = encrypt_stash(
            payload, aad=aad_for_redis_stash(stash_id_from_key(key)),
        )
        client = aioredis.from_url(settings.redis_url)
        try:
            await client.set(key, envelope, ex=300)
        finally:
            await client.aclose()
        written.append(key)
        return key

    yield _stash

    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        for key in written:
            try:
                await client.delete(key)
            except Exception:
                pass
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def _bypass_bmc_ssrf_guard(request, monkeypatch):
    """По умолчанию выключаем SSRF-guard `ensure_bmc_host_allowed` в тестах.

    Большая часть suite'а гоняет `get_bmc_client` против hostname'ов вроде
    `bmc.test`, которые не резолвятся в DNS — guard бы валил их с
    `BMC_ENDPOINT_BLOCKED` ещё до probe'а. Тест, который проверяет сам
    guard, навешивает маркер `enforce_bmc_ssrf_guard` и получает
    оригинальную функцию обратно.
    """
    if request.node.get_closest_marker("enforce_bmc_ssrf_guard"):
        return
    async def _noop(_host: str) -> None:
        return None
    monkeypatch.setattr("src.clients.ensure_bmc_host_allowed", _noop)


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
