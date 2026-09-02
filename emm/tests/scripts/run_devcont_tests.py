#!/usr/bin/env python3
"""
Run the full test suite in devcontainer mode (no Docker required).

Starts:
  - logging_service  on :8011  (test_logging DB)
  - auth_service     on :8010  (test_auth DB, talks to logging on :8011)
  - registry         on :5000  (token auth via auth_service)

Then runs, in order:
  1. auth_service unit + integration tests   (no e2e mark)
  2. logging_service tests
  3. cross-service integration tests
  4. E2E docker registry tests

Exit code is 0 only if all suites pass.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

REPO = Path(__file__).parent.parent.parent.resolve()
AUTH_DIR = REPO / "auth_service"
LOGGING_DIR = REPO / "loging_service"

AUTH_PORT = 8010
LOGGING_PORT = 8011
REGISTRY_PORT = 5000

AUTH_URL = f"http://localhost:{AUTH_PORT}"
LOGGING_URL = f"http://localhost:{LOGGING_PORT}"
REGISTRY_HOST = f"localhost:{REGISTRY_PORT}"

ADMIN_USERNAME = "e2e_admin"
ADMIN_PASSWORD = "E2eAdmin1234!"
LOGGING_API_KEY = "test-logging-api-key"

# Databases for running services (integration + E2E tests)
AUTH_TEST_DB     = "postgresql+psycopg://app_user:app_password@postgres:5432/test_auth"
LOGGING_TEST_DB  = "postgresql+psycopg://app_user:app_password@postgres:5432/test_logging"

# Isolated databases for unit tests (TestClient + SAVEPOINT rollback).
# Kept separate so teardown schema wipe doesn't affect running services.
AUTH_UNIT_DB    = "postgresql+psycopg://app_user:app_password@postgres:5432/test_auth_unit"
LOGGING_UNIT_DB = "postgresql+psycopg://app_user:app_password@postgres:5432/test_logging_unit"
SECRET_KEY = "test-secret-key-for-integration-must-be-32-chars!"

# ── Colours ───────────────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED   = "\033[31m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def ok(msg):  print(f"{GREEN}✓{RESET} {msg}")
def err(msg): print(f"{RED}✗{RESET} {msg}", file=sys.stderr)
def hdr(msg): print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def wait_healthy(url: str, retries: int = 40, delay: float = 1.0) -> None:
    for _ in range(retries):
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Service not healthy: {url}")


def wait_registry(host: str, retries: int = 40, delay: float = 1.0) -> None:
    for _ in range(retries):
        try:
            r = httpx.get(f"http://{host}/v2/", timeout=3)
            if r.status_code in (200, 401):
                return
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Registry not healthy: {host}")


def run_migrations(service_dir: Path, db_url: str) -> None:
    from sqlalchemy import create_engine, text
    from urllib.parse import urlparse

    # Reset schema
    parsed = urlparse(db_url)
    db_name = parsed.path.lstrip("/")
    admin_url = db_url.replace(f"/{db_name}", "/postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        conn.execute(text(f'DROP SCHEMA IF EXISTS public CASCADE'))
        conn.execute(text(f'CREATE SCHEMA public'))
    engine.dispose()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env={**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(service_dir)},
        check=True,
        capture_output=True,
    )


def seed_admin(service_dir: Path, db_url: str) -> None:
    subprocess.run(
        [sys.executable, "src/scripts/seed_e2e.py"],
        cwd=service_dir,
        env={
            **os.environ,
            "DATABASE_URL": db_url,
            "PYTHONPATH": str(service_dir),
            "SECRET_KEY": SECRET_KEY,
            "ACCESS_TOKEN_TTL_MINUTES": "10",
            "REFRESH_TOKEN_TTL_DAYS": "14",
            "E2E_ADMIN_USERNAME": ADMIN_USERNAME,
            "E2E_ADMIN_PASSWORD": ADMIN_PASSWORD,
        },
        check=True,
        capture_output=True,
    )


def start_process(cmd: list, cwd: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )


def kill_process(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        pass


def run_suite(label: str, cmd: list, cwd: Path, extra_env: dict | None = None) -> bool:
    hdr(label)
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode == 0:
        ok(f"{label} — PASSED")
    else:
        err(f"{label} — FAILED (exit {result.returncode})")
    return result.returncode == 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    registry_bin = shutil.which("registry")
    if not registry_bin:
        err("'registry' binary not found. Run: bash tests/scripts/install_registry.sh")
        return 1

    workdir = tempfile.mkdtemp(prefix="dbos_test_")
    procs: list[subprocess.Popen] = []

    try:
        # ── 1. Migrate both databases ─────────────────────────────────────────
        hdr("Preparing databases")
        print("  → migrating test_auth …")
        run_migrations(AUTH_DIR, AUTH_TEST_DB)
        print("  → migrating test_auth_unit …")
        run_migrations(AUTH_DIR, AUTH_UNIT_DB)
        print("  → migrating test_logging …")
        run_migrations(LOGGING_DIR, LOGGING_TEST_DB)
        print("  → migrating test_logging_unit …")
        run_migrations(LOGGING_DIR, LOGGING_UNIT_DB)
        print("  → seeding e2e_admin …")
        seed_admin(AUTH_DIR, AUTH_TEST_DB)
        ok("Databases ready")

        # ── 2. Start logging_service ──────────────────────────────────────────
        print(f"\n  → starting logging_service on :{LOGGING_PORT} …")
        procs.append(start_process(
            [sys.executable, "-m", "uvicorn", "src.main:app",
             "--host", "0.0.0.0", "--port", str(LOGGING_PORT)],
            LOGGING_DIR,
            {**os.environ, "DATABASE_URL": LOGGING_TEST_DB,
             "SERVICE_API_KEY": LOGGING_API_KEY,
             "PYTHONPATH": str(LOGGING_DIR), "APP_LOG_LEVEL": "WARNING"},
        ))
        wait_healthy(f"{LOGGING_URL}/api/logging/v1/health")
        ok(f"logging_service up on :{LOGGING_PORT}")

        # ── 3. Start auth_service ─────────────────────────────────────────────
        print(f"  → starting auth_service on :{AUTH_PORT} …")
        procs.append(start_process(
            [sys.executable, "-m", "uvicorn", "src.main:app",
             "--host", "0.0.0.0", "--port", str(AUTH_PORT)],
            AUTH_DIR,
            {**os.environ,
             "DATABASE_URL": AUTH_TEST_DB,
             "SECRET_KEY": SECRET_KEY,
             "ACCESS_TOKEN_TTL_MINUTES": "10",
             "REFRESH_TOKEN_TTL_DAYS": "14",
             "LOGGING_SERVICE_URL": LOGGING_URL,
             "LOGGING_SERVICE_API_KEY": LOGGING_API_KEY,
             "PYTHONPATH": str(AUTH_DIR)},
        ))
        wait_healthy(f"{AUTH_URL}/api/auth/v1/health")
        ok(f"auth_service up on :{AUTH_PORT}")

        # ── 4. Fetch PEM cert + start registry ────────────────────────────────
        print("  → fetching RSA public cert from auth_service …")
        pem = httpx.get(f"{AUTH_URL}/api/auth/v1/docker/certs", timeout=5).text
        cert_path = Path(workdir) / "auth.pem"
        cert_path.write_text(pem)

        registry_config = Path(workdir) / "config.yml"
        registry_config.write_text(f"""\
version: 0.1
log:
  level: error
storage:
  filesystem:
    rootdirectory: {workdir}/data
http:
  addr: :{REGISTRY_PORT}
auth:
  token:
    realm: {AUTH_URL}/api/auth/v1/docker/token
    service: {REGISTRY_HOST}
    issuer: auth_service
    rootcertbundle: {cert_path}
""")

        print(f"  → starting registry on :{REGISTRY_PORT} …")
        procs.append(start_process(
            [registry_bin, "serve", str(registry_config)],
            Path(workdir),
            {**os.environ},
        ))
        wait_registry(REGISTRY_HOST)
        ok(f"registry up on :{REGISTRY_PORT}")

        # ── 5. Run test suites ────────────────────────────────────────────────
        results: list[bool] = []

        results.append(run_suite(
            "auth_service — unit + integration",
            [sys.executable, "-m", "pytest", "tests/", "-m", "not e2e",
             "-v", "--tb=short"],
            AUTH_DIR,
            {"PYTHONPATH": str(AUTH_DIR),
             "TEST_DATABASE_URL": AUTH_UNIT_DB},
        ))

        results.append(run_suite(
            "logging_service — all tests",
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            LOGGING_DIR,
            {"PYTHONPATH": str(LOGGING_DIR),
             "TEST_DATABASE_URL": LOGGING_UNIT_DB},
        ))

        results.append(run_suite(
            "cross-service integration (auth ↔ logging)",
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            REPO,
            {"AUTH_SERVICE_URL": AUTH_URL,
             "LOGGING_SERVICE_URL": LOGGING_URL,
             "LOGGING_SERVICE_API_KEY": LOGGING_API_KEY,
             "E2E_ADMIN_USERNAME": ADMIN_USERNAME,
             "E2E_ADMIN_PASSWORD": ADMIN_PASSWORD},
        ))

        results.append(run_suite(
            "E2E — docker registry token auth",
            [sys.executable, "-m", "pytest", "tests/e2e/", "-m", "e2e",
             "-v", "--tb=short"],
            AUTH_DIR,
            {"PYTHONPATH": str(AUTH_DIR),
             "E2E_AUTH_SERVICE_URL": AUTH_URL,
             "E2E_REGISTRY_HOST": REGISTRY_HOST},
        ))

        # ── 6. Summary ────────────────────────────────────────────────────────
        hdr("Results")
        labels = [
            "auth unit+integration",
            "logging tests",
            "cross-service integration",
            "E2E docker registry",
        ]
        all_passed = True
        for label, passed in zip(labels, results):
            icon = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
            print(f"  {icon}  {label}")
            all_passed = all_passed and passed

        print()
        if all_passed:
            ok("All suites passed")
            return 0
        else:
            err("Some suites failed")
            return 1

    finally:
        for p in reversed(procs):
            kill_process(p)


if __name__ == "__main__":
    sys.exit(main())
