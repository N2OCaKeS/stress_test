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


# ════════════════════════════════════════════════════════════════════════════
# Full-stack E2E fixtures (server_service + server_worker + ssh-target)
# ════════════════════════════════════════════════════════════════════════════
#
# Active when the full integration compose is up. The compose injects
# `SERVER_SERVICE_URL`, *_DB_URL, REDIS_URL, SSH_* envs into the test-runner.
# In local devcontainer mode these fixtures will skip the tests that depend
# on them (via the `_require_full_stack` guard).
#
# Fixture map for Phase 1 scenario authors:
#
#   integration_stack       — sentinel session-scoped fixture; ensures every
#                              backend (4 services + ssh-target) is healthy
#                              and admin login works. Phase 1 tests don't
#                              need to depend on it explicitly — the per-
#                              client fixtures already do.
#
#   server_client           — httpx.Client(base_url=SERVER_SERVICE_URL).
#                              Use `with_token(server_client, token)` or
#                              pass `headers={"Authorization": "Bearer ..."}`
#                              per-request.
#   admin_server_client     — server_client with admin Bearer pre-set.
#
#   ssh_test_host           — dict describing the openssh-server target:
#                                {
#                                  "host": "ssh-target",
#                                  "port": 2222,
#                                  "root_login": "root",
#                                  "root_password": "test-root-pw",
#                                  "mgmt_user": "dbos",
#                                  "mgmt_pubkey": "ssh-ed25519 AAAA...",
#                                  "mgmt_privkey_path": "/keys/id_ed25519",
#                                }
#                              Use root_login+root_password to seed bootstrap
#                              credentials for `server.prepare`; after prepare,
#                              verify access via `mgmt_user` + mgmt_privkey_path.
#
#   auth_db_engine          — SQLAlchemy Engine bound to auth_db_test.
#   loging_db_engine        — SQLAlchemy Engine bound to logging_db_test.
#                              Phase 1 tests use this for direct
#                              `SELECT FROM audit_events WHERE action=...`
#                              after performing an action through the API,
#                              when polling /events would race.
#   server_db_engine        — SQLAlchemy Engine bound to server_db_test.
#   worker_db_engine        — SQLAlchemy Engine bound to worker_db_test.
#                              Inspect tasks / audit_outbox rows directly.
#
#   reset_state             — function-scoped fixture; truncates non-seed
#                              data between tests (servers / accounts /
#                              audit_events / tasks). Identities (admin,
#                              departments, bots) survive.
#
#   make_user(username=None, password="UserPass1!", platform_role=None,
#             department_id=None) -> dict
#                              Factory: creates a user via /users; returns
#                              the response body (incl. id). Cleans up on
#                              session teardown.
#   make_bot(name=None, department_id, allowed_services=None,
#            service_roles=None) -> dict
#                              Factory: creates a bot, returns bot record
#                              with `bot_id`.
#   login_token(username, password) -> str
#                              Helper: login a user and return access_token.
#   pat_token(user_token, name=None, scope=None, ttl_seconds=3600) -> str
#                              Helper: issue a PAT under the given user.
#
#   ssh_session(target=ssh_test_host) -> paramiko.SSHClient
#                              Helper context manager that opens a
#                              password-auth root session to ssh-target. Use
#                              for direct host assertions (`whoami`,
#                              `getent passwd dbos`, reading
#                              authorized_keys, etc.).

_FULL_STACK = "SERVER_SERVICE_URL" in os.environ
_SSH_TARGET_HOST = os.environ.get("SSH_TARGET_HOST", "ssh-target")
_SSH_TARGET_PORT = int(os.environ.get("SSH_TARGET_PORT", "2222"))
_SSH_TARGET_ROOT_USER = os.environ.get("SSH_TARGET_ROOT_USER", "root")
_SSH_TARGET_ROOT_PASSWORD = os.environ.get("SSH_TARGET_ROOT_PASSWORD", "test-root-pw")
_SSH_MGMT_USER = os.environ.get("SSH_MGMT_USER", "dbos")
_SSH_MGMT_PRIVATE_KEY_PATH = os.environ.get("SSH_MGMT_PRIVATE_KEY_PATH", "/keys/id_ed25519")
_SSH_MGMT_PUBLIC_KEY_PATH = _SSH_MGMT_PRIVATE_KEY_PATH + ".pub" if _SSH_MGMT_PRIVATE_KEY_PATH else ""
_SERVER_URL = os.environ.get("SERVER_SERVICE_URL", "http://localhost:8012")
_DBOS_ADMIN_USERNAME = os.environ.get("DBOS_ADMIN_USERNAME", ADMIN_USERNAME)
_DBOS_ADMIN_PASSWORD = os.environ.get("DBOS_ADMIN_PASSWORD", ADMIN_PASSWORD)


def _require_full_stack() -> None:
    """Skip the calling test when the full E2E compose isn't running."""
    if not _FULL_STACK:
        pytest.skip("Full integration stack not running (SERVER_SERVICE_URL unset)")


@pytest.fixture(scope="session")
def integration_stack() -> dict:
    """Sentinel: confirms every backend in the E2E compose is reachable.

    Phase 1 tests usually do not need to depend on this directly — per-
    component fixtures (`server_client`, `ssh_test_host`, *_db_engine)
    already establish the dependency chain. Use it explicitly when a test
    only wants to assert "stack is alive" without further setup.
    """
    _require_full_stack()
    _wait_healthy(AUTH_URL, "/api/auth/v1/health")
    _wait_healthy(LOGGING_URL, "/api/logging/v1/health")
    _wait_healthy(_SERVER_URL, "/api/server/v1/health")
    return {
        "auth_url": AUTH_URL,
        "logging_url": LOGGING_URL,
        "server_url": _SERVER_URL,
        "ssh_host": _SSH_TARGET_HOST,
        "ssh_port": _SSH_TARGET_PORT,
    }


@pytest.fixture(scope="session")
def server_client(integration_stack) -> httpx.Client:
    """Unauthenticated server_service HTTP client. Pass Bearer per-request."""
    with httpx.Client(base_url=_SERVER_URL, timeout=15) as client:
        yield client


@pytest.fixture(scope="session")
def admin_server_client(integration_stack, admin_token: str) -> httpx.Client:
    """server_service client pre-authed as the platform `account_admin`."""
    with httpx.Client(
        base_url=_SERVER_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    ) as client:
        yield client


def _load_mgmt_pubkey() -> str:
    if not _SSH_MGMT_PUBLIC_KEY_PATH:
        return ""
    try:
        with open(_SSH_MGMT_PUBLIC_KEY_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


@pytest.fixture(scope="session")
def ssh_test_host(integration_stack) -> dict:
    """Connection details for the openssh-server scenario target.

    Use root_login+root_password as the bootstrap creds passed to
    `server.prepare`. After prepare succeeds, the management user
    (`mgmt_user`) should be reachable with `mgmt_privkey_path` over SSH key
    auth on the same port.
    """
    pubkey = _load_mgmt_pubkey()
    if not pubkey:
        pytest.skip(f"Management pubkey not found at {_SSH_MGMT_PUBLIC_KEY_PATH}")
    return {
        "host": _SSH_TARGET_HOST,
        "port": _SSH_TARGET_PORT,
        "root_login": _SSH_TARGET_ROOT_USER,
        "root_password": _SSH_TARGET_ROOT_PASSWORD,
        "mgmt_user": _SSH_MGMT_USER,
        "mgmt_pubkey": pubkey,
        "mgmt_privkey_path": _SSH_MGMT_PRIVATE_KEY_PATH,
    }


# ── Direct DB engines (for assertions that can't go through the API) ──────

def _engine_from_env(var: str):
    from sqlalchemy import create_engine
    url = os.environ.get(var)
    if not url:
        pytest.skip(f"{var} not set; full integration stack required")
    return create_engine(url, pool_pre_ping=True, future=True)


@pytest.fixture(scope="session")
def auth_db_engine():
    """SQLAlchemy Engine for the auth_service DB (read-only assertions)."""
    engine = _engine_from_env("AUTH_DB_URL")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def loging_db_engine():
    """SQLAlchemy Engine for the logging_service DB.

    Use for direct queries against `audit_events` when you need
    deterministic ordering or filtering beyond `GET /events` (e.g.
    inspecting `details` JSONB). Standard pattern::

        from sqlalchemy import text
        with loging_db_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT * FROM audit_events "
                "WHERE action = :a ORDER BY timestamp DESC LIMIT 5"
            ), {"a": "server.prepare.success"}).mappings().all()
    """
    engine = _engine_from_env("LOGGING_DB_URL")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def server_db_engine():
    """SQLAlchemy Engine for the server_service DB."""
    engine = _engine_from_env("SERVER_DB_URL")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def worker_db_engine():
    """SQLAlchemy Engine for the server_worker DB.

    Use for inspecting `tasks` rows (status, attempts, last_error) and
    `audit_outbox` rows after a task fires.
    """
    engine = _engine_from_env("WORKER_DB_URL")
    yield engine
    engine.dispose()


# ── Identity factories ────────────────────────────────────────────────────

_RANDOM_SUFFIX_LEN = 8


def _rand_suffix() -> str:
    import secrets
    return secrets.token_hex(_RANDOM_SUFFIX_LEN // 2)


@pytest.fixture(scope="session")
def make_user(auth_client: httpx.Client, admin_token: str):
    """Factory: create a user via auth_service. Returns response body.

    Usage::

        u = make_user(platform_role="loging_reader")
        token = login_token(u["username"], "UserPass1!")
    """
    created: list[str] = []

    def _make(
        username: str | None = None,
        password: str = "UserPass1!",
        platform_role: str | None = None,
        department_id: str | None = None,
        **extra,
    ) -> dict:
        body = {
            "username": username or f"u_{_rand_suffix()}",
            "password": password,
            **extra,
        }
        if platform_role is not None:
            body["platform_role"] = platform_role
        if department_id is not None:
            body["department_id"] = department_id
        r = auth_client.post(
            "/api/auth/v1/users",
            json=body,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code in (200, 201), f"make_user failed: {r.status_code} {r.text}"
        data = r.json()
        created.append(data.get("id") or data.get("user_id") or body["username"])
        # Always include username/password so the caller can immediately log in.
        data.setdefault("username", body["username"])
        data["_password"] = password
        return data

    yield _make
    # No cleanup: identities are cheap and may be referenced by audit rows.


@pytest.fixture(scope="session")
def make_bot(auth_client: httpx.Client, admin_token: str):
    """Factory: create a bot. Returns bot record (incl. bot_id)."""

    def _make(
        name: str | None = None,
        department_id: str | None = None,
        allowed_services: list[str] | None = None,
        service_roles: list[dict] | None = None,
        **extra,
    ) -> dict:
        body = {
            "name": name or f"bot_{_rand_suffix()}",
            **extra,
        }
        if department_id is not None:
            body["department_id"] = department_id
        if allowed_services is not None:
            body["allowed_services"] = allowed_services
        if service_roles is not None:
            body["service_roles"] = service_roles
        r = auth_client.post(
            "/api/auth/v1/bots",
            json=body,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code in (200, 201), f"make_bot failed: {r.status_code} {r.text}"
        return r.json()

    return _make


@pytest.fixture(scope="session")
def login_token(auth_client: httpx.Client):
    """Helper: log in a username/password pair, return the access_token."""

    def _login(username: str, password: str) -> str:
        r = auth_client.post(
            "/api/auth/v1/login",
            json={"username": username, "password": password},
        )
        assert r.status_code == 200, f"login({username}) failed: {r.text}"
        return r.json()["access_token"]

    return _login


@pytest.fixture(scope="session")
def pat_token(auth_client: httpx.Client):
    """Helper: issue a Personal Access Token under the given user's JWT.

    Returns the plaintext PAT (shown exactly once).
    """

    def _issue(
        user_access_token: str,
        name: str | None = None,
        scope: list[str] | None = None,
        ttl_seconds: int | None = 3600,
    ) -> str:
        body: dict = {"name": name or f"pat_{_rand_suffix()}"}
        if scope is not None:
            body["scope"] = scope
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        r = auth_client.post(
            "/api/auth/v1/tokens",
            json=body,
            headers={"Authorization": f"Bearer {user_access_token}"},
        )
        assert r.status_code in (200, 201), f"pat_token failed: {r.status_code} {r.text}"
        data = r.json()
        token = data.get("token") or data.get("plaintext")
        assert token, f"PAT response missing token: {data}"
        return token

    return _issue


# ── SSH helper ────────────────────────────────────────────────────────────

@pytest.fixture
def ssh_session(ssh_test_host):
    """Context-manager helper that opens a paramiko SSH session to the target.

    Default: root + password (bootstrap credentials). Pass `as_mgmt=True`
    to use the management user + private key (post-prepare verification).

    Usage::

        with ssh_session() as ssh:
            _, stdout, _ = ssh.exec_command("getent passwd dbos")
            assert b"dbos:" in stdout.read()
    """
    import contextlib
    import paramiko

    @contextlib.contextmanager
    def _open(as_mgmt: bool = False):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if as_mgmt:
                pkey = paramiko.Ed25519Key.from_private_key_file(
                    ssh_test_host["mgmt_privkey_path"]
                )
                client.connect(
                    hostname=ssh_test_host["host"],
                    port=ssh_test_host["port"],
                    username=ssh_test_host["mgmt_user"],
                    pkey=pkey,
                    look_for_keys=False,
                    allow_agent=False,
                    timeout=10,
                )
            else:
                client.connect(
                    hostname=ssh_test_host["host"],
                    port=ssh_test_host["port"],
                    username=ssh_test_host["root_login"],
                    password=ssh_test_host["root_password"],
                    look_for_keys=False,
                    allow_agent=False,
                    timeout=10,
                )
            yield client
        finally:
            client.close()

    return _open


# ── State reset between tests ─────────────────────────────────────────────

# Tables wiped by `reset_state`. Order matters for FK dependencies — child
# tables first. Identities (users / departments / bots / service catalog)
# survive so factories aren't re-run on every test.
_RESET_TABLES_BY_DB = {
    "server_db_engine": [
        "server_accounts",
        "ipmi_controllers",
        "servers",
    ],
    "worker_db_engine": [
        "audit_outbox",
        "tasks",
    ],
    "loging_db_engine": [
        # Keep `service_events` (catalog) and `audit_rules` between tests.
        "audit_events",
    ],
}


@pytest.fixture
def reset_state(server_db_engine, worker_db_engine, loging_db_engine):
    """Truncate scenario-data tables before the test.

    Wipes servers / accounts / tasks / audit_events; preserves identities,
    department/service catalog, audit rules. Yields after truncation so
    the test runs against a clean slate.
    """
    from sqlalchemy import text

    engines = {
        "server_db_engine": server_db_engine,
        "worker_db_engine": worker_db_engine,
        "loging_db_engine": loging_db_engine,
    }
    for key, tables in _RESET_TABLES_BY_DB.items():
        engine = engines[key]
        with engine.begin() as conn:
            for tbl in tables:
                conn.execute(text(f'TRUNCATE TABLE "{tbl}" RESTART IDENTITY CASCADE'))
    yield
