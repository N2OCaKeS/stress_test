#!/usr/bin/env python3
"""Декларативный seed для auth_service из JSON-файла.

Использование:
    python3 scripts/seed_from_json.py scripts/seed_template.json

Скрипт идемпотентен — повторный запуск на наполненной БД не падает
(409/duplicate переводится в OK).

JSON-схема — см. seed_template.json. Связи between сущностями выражены через
name'ы (department_name / role_name / username), резолв в id делается
скриптом на лету через GET endpoint'ы.
"""

import json
import os
import sys

import httpx

AUTH_URL = os.environ.get("AUTH_URL", "http://localhost:8000")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client: httpx.Client, username: str, password: str) -> str:
    r = client.post(
        f"{AUTH_URL}/api/auth/v1/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _skip(msg: str) -> None:
    print(f"  · {msg}")


def _err(msg: str, body: dict | None = None) -> None:
    if body:
        ec = body.get("error_code") or body.get("error")
        m = body.get("message") or body.get("detail")
        details = body.get("details") or {}
        errs = details.get("errors") if isinstance(details, dict) else None
        extra = ""
        if errs and isinstance(errs, list):
            parts = []
            for e in errs[:3]:
                loc = ".".join(str(x) for x in e.get("loc", []))
                parts.append(f"{loc}={e.get('msg', '')}")
            extra = " | " + " ; ".join(parts)
        msg = (
            f"{msg} — {ec}: {m}{extra}" if ec or m else f"{msg} — {body}"
        )
    print(f"  ✗ {msg}", file=sys.stderr)


def _post(client: httpx.Client, path: str, body: dict, token: str) -> tuple[int, dict | None]:
    r = client.post(
        f"{AUTH_URL}{path}", json=body, headers=_bearer(token), timeout=15
    )
    try:
        data = r.json() if r.content else None
    except Exception:
        data = None
    return r.status_code, data


def _get(client: httpx.Client, path: str, token: str) -> list | dict | None:
    r = client.get(f"{AUTH_URL}{path}", headers=_bearer(token), timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def find_dept_id(client: httpx.Client, token: str, name: str | None) -> str | None:
    if not name:
        return None
    deps = _get(client, "/api/auth/v1/departments", token) or []
    for d in deps:
        if d.get("name") == name:
            return d.get("department_id") or d.get("id")
    return None


def find_user_id(client: httpx.Client, token: str, username: str) -> str | None:
    users = _get(client, "/api/auth/v1/users", token) or []
    for u in users:
        if u.get("username") == username:
            return u.get("user_id") or u.get("id")
    return None


def find_bot_id(client: httpx.Client, token: str, name: str) -> str | None:
    bots = _get(client, "/api/auth/v1/bots", token) or []
    for b in bots:
        if b.get("name") == name:
            return b.get("bot_id") or b.get("id")
    return None


def seed_departments(client: httpx.Client, token: str, items: list) -> None:
    print("\nDepartments:")
    for d in items:
        status, body = _post(
            client,
            "/api/auth/v1/departments",
            {"name": d["name"]},
            token,
        )
        if status in (200, 201):
            _ok(f"created {d['name']}")
        elif status == 409:
            _skip(f"exists  {d['name']}")
        else:
            _err(f"{d['name']}: {status}", body)


def seed_users(client: httpx.Client, token: str, items: list) -> None:
    print("\nUsers:")
    for u in items:
        dept_id = find_dept_id(client, token, u.get("department_name"))
        payload = {
            "username": u["username"],
            "password": u["password"],
            "email": u.get("email"),
            "department_id": dept_id,
            "platform_role": u.get("platform_role"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        status, resp = _post(client, "/api/auth/v1/users", payload, token)
        if status in (200, 201):
            _ok(f"created {u['username']}")
        elif status == 409:
            _skip(f"exists  {u['username']}")
        else:
            _err(f"{u['username']}: {status}", resp)


def seed_groups(client: httpx.Client, token: str, items: list) -> None:
    print("\nGroups:")
    for g in items:
        dept_id = find_dept_id(client, token, g.get("department_name"))
        if not dept_id:
            _err(f"{g['name']}: department '{g.get('department_name')}' not found")
            continue
        status, group_resp = _post(
            client,
            "/api/auth/v1/groups",
            {
                "department_id": dept_id,
                "name": g["name"],
                "description": g.get("description"),
            },
            token,
        )
        if status in (200, 201):
            _ok(f"created {g['name']}")
        elif status == 409:
            _skip(f"exists  {g['name']}")
        else:
            _err(f"{g['name']}: {status}", body)
            continue

        # resolve group_id для добавления членов
        groups = _get(client, "/api/auth/v1/groups", token) or []
        gid = next(
            (x.get("group_id") or x.get("id") for x in groups if x.get("name") == g["name"]),
            None,
        )
        if not gid:
            _err(f"{g['name']}: group_id not resolved")
            continue
        for username in g.get("members") or []:
            uid = find_user_id(client, token, username)
            if not uid:
                _err(f"{g['name']}: member {username} not found")
                continue
            status, body = _post(
                client,
                f"/api/auth/v1/groups/{gid}/members",
                {"user_id": uid},
                token,
            )
            if status in (200, 201):
                _ok(f"  + member {username}")
            elif status == 409:
                _skip(f"  · member {username} already in group")
            else:
                _err(f"  ✗ add member {username}: {status}", body)


def seed_bots(client: httpx.Client, token: str, items: list) -> None:
    print("\nBots:")
    for b in items:
        dept_id = find_dept_id(client, token, b.get("department_name"))
        if not dept_id:
            _err(f"{b['name']}: department '{b.get('department_name')}' not found")
            continue
        payload = {
            "name": b["name"],
            "department_id": dept_id,
            "allowed_services": b.get("allowed_services") or [],
            "description": b.get("description"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        status, resp = _post(client, "/api/auth/v1/bots", payload, token)
        if status in (200, 201):
            _ok(f"created {b['name']}")
        elif status == 409:
            _skip(f"exists  {b['name']}")
        else:
            _err(f"{b['name']}: {status}", resp)


def seed_department_services(client: httpx.Client, token: str, items: list) -> None:
    print("\nDepartment → services:")
    for d in items:
        dept_id = find_dept_id(client, token, d.get("department_name"))
        if not dept_id:
            _err(f"{d.get('department_name')}: not found")
            continue
        svc = d["service_name"]
        status, body = _post(
            client,
            f"/api/auth/v1/departments/{dept_id}/services",
            {"service_name": svc},
            token,
        )
        if status in (200, 201, 204):
            _ok(f"{d['department_name']} → {svc}")
        elif status == 409:
            _skip(f"{d['department_name']} → {svc} already granted")
        else:
            _err(f"{d['department_name']} → {svc}: {status}", body)


def seed_service_roles(client: httpx.Client, token: str, items: list) -> None:
    print("\nService roles:")
    for r in items:
        dept_id = find_dept_id(client, token, r.get("department_name"))
        if not dept_id:
            _err(f"{r['role_name']}: dept '{r.get('department_name')}' not found")
            continue
        svc = r["service_name"]
        body = {
            "role_name": r["role_name"],
            "description": r.get("description"),
        }
        body = {k: v for k, v in body.items() if v is not None}
        status, body = _post(
            client,
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles",
            body,
            token,
        )
        if status in (200, 201):
            _ok(f"{svc}/{r['role_name']} in {r['department_name']}")
        elif status == 409:
            _skip(f"{svc}/{r['role_name']} in {r['department_name']} exists")
        else:
            _err(f"{svc}/{r['role_name']}: {status}", body)


def seed_role_assignments(client: httpx.Client, token: str, items: list) -> None:
    print("\nRole assignments:")
    for a in items:
        dept_id = find_dept_id(client, token, a.get("department_name"))
        if not dept_id:
            _err(f"assignment skip: dept '{a.get('department_name')}' not found")
            continue
        svc = a["service_name"]
        role = a["role_name"]
        user_ids = [
            find_user_id(client, token, u) for u in a.get("usernames") or []
        ]
        user_ids = [u for u in user_ids if u]
        # Backend BulkRoleRequest принимает только user_ids — боты получают
        # доступ к сервису через allowed_services, не через service-роль.
        # bot_names в JSON игнорируются с предупреждением.
        if a.get("bot_names"):
            _skip(
                f"{svc}/{role}@{a['department_name']}: bot_names игнорируются "
                f"(backend assign accepts only user_ids)"
            )
        if not user_ids:
            _skip(f"{svc}/{role} в {a['department_name']}: нет валидных user_ids")
            continue
        body = {"user_ids": user_ids}
        status, body = _post(
            client,
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles/{role}/assign",
            body,
            token,
        )
        if status in (200, 201, 204):
            _ok(
                f"{svc}/{role}@{a['department_name']} → users={len(user_ids)}"
            )
        elif status == 409:
            _skip(f"{svc}/{role}@{a['department_name']} already assigned")
        else:
            _err(f"{svc}/{role}@{a['department_name']}: {status}", body)


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("Usage: seed_from_json.py <seed.json>")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    global AUTH_URL  # noqa: PLW0603 — override default if meta specifies

    meta = data.get("_meta", {})
    admin_user = meta.get("admin_username", "admin")
    admin_pass = meta.get("admin_password", "1234")
    AUTH_URL = meta.get("auth_url") or AUTH_URL

    print(f"AUTH_URL: {AUTH_URL}")
    print(f"Logging in as {admin_user}…")

    # trust_env=False — ignore HTTP_PROXY/HTTPS_PROXY (auth_service is local).
    with httpx.Client(trust_env=False) as client:
        token = login(client, admin_user, admin_pass)
        _ok(f"logged in")

        if data.get("departments"):
            seed_departments(client, token, data["departments"])
        if data.get("users"):
            seed_users(client, token, data["users"])
        if data.get("groups"):
            seed_groups(client, token, data["groups"])
        if data.get("bots"):
            seed_bots(client, token, data["bots"])
        if data.get("department_services"):
            seed_department_services(client, token, data["department_services"])
        if data.get("service_roles"):
            seed_service_roles(client, token, data["service_roles"])
        if data.get("role_assignments"):
            seed_role_assignments(client, token, data["role_assignments"])

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
