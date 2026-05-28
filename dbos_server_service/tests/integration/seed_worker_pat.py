"""Minimal bootstrap for the integration stack.

Creates exactly what's needed for `server-worker` to start:

1. Logs in as the admin (`INITIAL_ADMIN_USERNAME` / `_PASSWORD`).
2. Creates a department `it`.
3. Creates a bot `worker_bot_it` in that department, scoped to
   `server_service` (the `worker_bot` role is seeded by a server_service
   migration — we just attach it).
4. Issues a PAT for that bot.
5. Writes the PAT to `/shared/.worker_pat`.

Idempotent: if anything already exists, skips it. The scenario tests can
create whatever extra fixtures they need on top of this minimum.
"""

from __future__ import annotations

import os
import sys
import time

import httpx


AUTH_URL = os.environ.get("AUTH_URL", "http://auth-service:8000")
SERVER_URL = os.environ.get("SERVER_URL", "http://server-service:8002")
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")
SHARED_DIR = os.environ.get("SHARED_DIR", "/shared")
PAT_FILE = os.path.join(SHARED_DIR, ".worker_pat")

DEPT_NAME = "it"
BOT_NAME = "worker_bot_it"
PAT_NAME = "server_worker_pat"


def _wait_health(url: str, path: str, label: str, retries: int = 60) -> None:
    for _ in range(retries):
        try:
            if httpx.get(f"{url}{path}", timeout=3).status_code == 200:
                print(f"  + {label} healthy")
                return
        except Exception:
            pass
        time.sleep(1)
    sys.exit(f"  ! {label} did not become healthy at {url}{path}")


def _login(client: httpx.Client) -> str:
    r = client.post(
        "/api/auth/v1/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    if r.status_code != 200:
        sys.exit(f"  ! admin login failed: HTTP {r.status_code} — {r.text}")
    return r.json()["access_token"]


def _find_dept(client: httpx.Client, name: str) -> dict | None:
    r = client.get("/api/auth/v1/departments")
    if r.status_code != 200:
        return None
    data = r.json()
    items = data.get("items") if isinstance(data, dict) else data
    for d in (items or []):
        if d.get("name") == name:
            return d
    return None


def _ensure_dept(client: httpx.Client, name: str) -> str:
    existing = _find_dept(client, name)
    if existing:
        dept_id = existing.get("department_id") or existing.get("id")
        print(f"  = department {name} (already exists, id={dept_id})")
        return dept_id
    r = client.post(
        "/api/auth/v1/departments",
        json={"name": name, "display_name": name.upper()},
    )
    if r.status_code not in (200, 201):
        sys.exit(f"  ! create department: HTTP {r.status_code} — {r.text}")
    body = r.json()
    dept_id = body.get("department_id") or body.get("id")
    print(f"  + department {name} created (id={dept_id})")
    return dept_id


def _ensure_service_registered(client: httpx.Client, service: str) -> None:
    r = client.get("/api/auth/v1/services")
    if r.status_code == 200:
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else data
        for entry in (items or []):
            if entry.get("service_name") == service:
                print(f"  = service {service} already registered")
                return
    r = client.post(
        "/api/auth/v1/services",
        json={"service_name": service, "display_name": service.replace("_", " ").title()},
    )
    if r.status_code in (200, 201):
        print(f"  + service {service} registered")
        return
    if r.status_code == 409:
        print(f"  = service {service} already registered (409)")
        return
    sys.exit(f"  ! register service {service}: HTTP {r.status_code} — {r.text}")


def _ensure_service_access(client: httpx.Client, dept_id: str, service: str) -> None:
    r = client.get(f"/api/auth/v1/departments/{dept_id}/services")
    if r.status_code == 200:
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else data
        for entry in (items or []):
            if entry.get("service_name") == service and entry.get("enabled"):
                print(f"  = service {service} already granted to {dept_id}")
                return
    r = client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        json={"service_name": service},
    )
    if r.status_code in (200, 201):
        print(f"  + service {service} granted to {dept_id}")
        return
    if r.status_code == 409:
        print(f"  = service {service} already granted to {dept_id}")
        return
    sys.exit(f"  ! grant {service}: HTTP {r.status_code} — {r.text}")


def _find_bot(client: httpx.Client, name: str) -> dict | None:
    r = client.get("/api/auth/v1/bots")
    if r.status_code != 200:
        return None
    data = r.json()
    items = data.get("items") if isinstance(data, dict) else data
    for b in (items or []):
        if b.get("name") == name:
            return b
    return None


def _ensure_bot(client: httpx.Client, dept_id: str) -> str:
    existing = _find_bot(client, BOT_NAME)
    if existing:
        print(f"  = bot {BOT_NAME} (already exists, id={existing['bot_id']})")
        return existing["bot_id"]
    body = {
        "name": BOT_NAME,
        "department_id": dept_id,
        "allowed_services": ["server_service"],
        "service_roles": [
            {"service_name": "server_service", "roles": ["worker_bot"]},
        ],
    }
    r = client.post("/api/auth/v1/bots", json=body)
    if r.status_code not in (200, 201):
        sys.exit(f"  ! create bot: HTTP {r.status_code} — {r.text}")
    bot_id = r.json()["bot_id"]
    print(f"  + bot {BOT_NAME} created (id={bot_id})")
    return bot_id


def _issue_pat(client: httpx.Client, bot_id: str) -> str:
    # PAT names must be unique per bot — append an epoch suffix so reruns
    # of the seeder against a persistent stack still succeed.
    name = f"{PAT_NAME}_{int(time.time())}"
    r = client.post(
        f"/api/auth/v1/bots/{bot_id}/tokens",
        json={"name": name},
    )
    if r.status_code not in (200, 201):
        sys.exit(f"  ! issue bot PAT: HTTP {r.status_code} — {r.text}")
    token = r.json().get("token")
    if not token:
        sys.exit(f"  ! bot PAT response missing 'token': {r.json()}")
    print(f"  + PAT issued ({token[:24]}...)")
    return token


def main() -> None:
    print("[seed-worker-pat] waiting for services...")
    _wait_health(AUTH_URL, "/api/auth/v1/health", "auth-service")
    _wait_health(SERVER_URL, "/api/server/v1/health", "server-service")

    admin_client = httpx.Client(base_url=AUTH_URL, timeout=15)
    admin_token = _login(admin_client)
    admin_client.headers["Authorization"] = f"Bearer {admin_token}"

    dept_id = _ensure_dept(admin_client, DEPT_NAME)
    _ensure_service_registered(admin_client, "server_service")
    _ensure_service_access(admin_client, dept_id, "server_service")
    bot_id = _ensure_bot(admin_client, dept_id)
    pat = _issue_pat(admin_client, bot_id)

    os.makedirs(SHARED_DIR, exist_ok=True)
    with open(PAT_FILE, "w") as f:
        f.write(pat)
    print(f"  + PAT written to {PAT_FILE}")


if __name__ == "__main__":
    main()
