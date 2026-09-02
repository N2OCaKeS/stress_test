"""Хелперы для G-кластера: inventory + warn-on-drift.

Содержит фасад поверх auth_service / server_service, без которого каждый
e2e-сценарий пришлось бы повторять одно и то же:

* `setup_dept_admin` — создаёт пользователя c системной admin-ролью в
  департаменте `it` (его уже завёл seeder), возвращает JWT под этим
  пользователем + dept_id;
* `register_server` — POST /servers под dept-admin, возвращает server-row
  целиком (включая id и department_id);
* `prepare_server` — гонит F-flow: dispatch /prepare → дождаться SUCCEEDED →
  убедиться, что server.is_managed=True;
* `create_account` — POST /server-accounts с привязкой к серверу;
* `provision_account_via_ssh` — обходит F.provision: создаёт OS-пользователя
  прямо через ssh_session, чтобы стейт бокса был стартово синхронизирован
  с БД (нам нужен именно factual-state для drift-сценариев);
* `box_useradd` / `box_usermod_sudo` / `box_userdel` — стейт-manipulation для
  drift сценариев (а/б/в);
* `dispatch_inventory_sync` / `dispatch_users_inventory` — POST dispatch +
  возврат task_id;
* `wait_task_status` — поллит worker-DB до SUCCEEDED/FAILED;
* `wait_inventory_audit` — расширение wait_for_event под нашу пачку
  inventory-action'ов (учитывает поллинг по details.server_id).

Стиль и зависимости — из существующего conftest.py: httpx синхронный
клиент, paramiko ssh_session, прямые DB-engine'ы.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx
from sqlalchemy import text


# ── Identity / department setup ─────────────────────────────────────────────

DEPT_NAME = "it"  # тот же отдел, что у seed_worker_pat


def find_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Найти department_id по имени; падать если не нашли."""
    r = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, f"list departments failed: {r.text}"
    body = r.json()
    items = body.get("items") if isinstance(body, dict) else body
    for d in items or []:
        if d.get("name") == name:
            return d.get("department_id") or d.get("id")
    raise AssertionError(f"department {name!r} not found")


def grant_service_role(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    service_name: str,
    role_name: str,
    user_id: str,
) -> None:
    """Bulk-assign системной роли пользователю.

    Системная `admin` сеется server_service'ом при выдаче департаменту
    доступа к сервису, тут только привязываем её к юзеру.
    """
    r = auth_client.post(
        f"/api/auth/v1/departments/{department_id}/services/{service_name}/roles/{role_name}/assign",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_id]},
    )
    assert r.status_code in (200, 201), (
        f"assign role {role_name} to {user_id} failed: {r.status_code} {r.text}"
    )


def setup_dept_admin(
    auth_client: httpx.Client,
    admin_token: str,
    make_user,
    login_token,
) -> dict:
    """Создать пользователя с admin-ролью в `server_service` (dept it).

    Возвращает: `{user_id, department_id, token, username}`.
    `make_user` и `login_token` — фикстуры из conftest (session-scoped).
    """
    dept_id = find_department(auth_client, admin_token, DEPT_NAME)
    # seed_worker_pat уже выдал dept it доступ к server_service и засеял
    # системные роли (admin/operator/reader/guest/worker_bot). Создаём
    # юзера в этом отделе и навешиваем admin.
    user = make_user(
        department_id=dept_id,
        allowed_services=["server_service"],
        service_roles=[{"service_name": "server_service", "roles": ["admin"]}],
    )
    user_id = user.get("id") or user.get("user_id")
    username = user["username"]
    password = user["_password"]
    # На случай, если make_user не пропустил service_roles в auth (схема
    # POST /users в разное время принимала/игнорировала это поле) —
    # доназначаем явным assign'ом.
    grant_service_role(
        auth_client, admin_token,
        department_id=dept_id, service_name="server_service",
        role_name="admin", user_id=user_id,
    )
    token = login_token(username, password)
    return {
        "user_id": user_id,
        "department_id": dept_id,
        "username": username,
        "token": token,
    }


# ── Server CRUD ─────────────────────────────────────────────────────────────


def _rand_suffix() -> str:
    return secrets.token_hex(4)


def register_server(
    server_client: httpx.Client,
    token: str,
    *,
    department_id: str,
    hostname: str | None = None,
    ip_address: str | None = None,
    ssh_port: int = 2222,
) -> dict:
    """POST /servers (под dept-admin). Возвращает карточку сервера.

    Дефолтный hostname/IP — рандомные, чтобы тесты не толкали друг друга в
    SERVER_DUPLICATE при параллельном запуске.
    """
    suffix = _rand_suffix()
    body = {
        "hostname": hostname or f"g-inv-{suffix}.test",
        "ip_address": ip_address or _rand_ip(),
        "ssh_port": ssh_port,
        "department_id": department_id,
    }
    r = server_client.post(
        "/api/server/v1/servers",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert r.status_code == 201, f"create server failed: {r.status_code} {r.text}"
    return r.json()


def _rand_ip() -> str:
    """Случайный IP в 10.x.x.x — UNIQUE-конфликт в server_db_test минимизируем
    разводя по приватному /8."""
    import random
    return "10.{}.{}.{}".format(
        random.randint(1, 254), random.randint(0, 254), random.randint(1, 254),
    )


# ── Prepare (F-flow) — без него inventory под управлением dbosroot ──────────


def _b64(text_value: str) -> str:
    import base64
    return base64.b64encode(text_value.encode("utf-8")).decode("ascii")


def dispatch_prepare(
    server_client: httpx.Client,
    token: str,
    *,
    server_id: str,
    username: str,
    password: str,
) -> str:
    """POST /servers/{id}/prepare → возвращает task_id."""
    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username_b64": _b64(username),
            "password_b64": _b64(password),
        },
    )
    assert r.status_code == 202, f"prepare dispatch failed: {r.status_code} {r.text}"
    return r.json()["task_id"]


# ── Server accounts ─────────────────────────────────────────────────────────


def create_account(
    server_client: httpx.Client,
    token: str,
    *,
    server_id: str,
    login: str,
    password: str | None = None,
    has_sudo: bool = False,
    unix_groups: list[str] | None = None,
    shell: str | None = None,
) -> dict:
    """POST /server-accounts с привязкой к одному серверу."""
    body: dict[str, Any] = {
        "server_ids": [server_id],
        "login": login,
        "has_sudo": has_sudo,
        "unix_groups": unix_groups or [],
    }
    if password is not None:
        body["password"] = password
    if shell is not None:
        body["shell"] = shell
    r = server_client.post(
        "/api/server/v1/server-accounts",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert r.status_code == 201, (
        f"create account {login!r} failed: {r.status_code} {r.text}"
    )
    return r.json()


# ── SSH-state manipulation (для drift-сценариев) ────────────────────────────


def _exec(ssh_client, cmd: str) -> tuple[int, str, str]:
    """Запуск команды на ssh-target, возврат (rc, stdout, stderr).

    На linuxserver/openssh-server bootstrap-юзер `dbosroot` — sudoer (см.
    compose: SUDO_ACCESS=true). Управляющие команды (useradd/usermod/userdel)
    идут через sudo с `echo password | sudo -S`.
    """
    _, stdout, stderr = ssh_client.exec_command(cmd, timeout=15)
    rc = stdout.channel.recv_exit_status()
    return rc, stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")


def _sudo(password: str, body: str) -> str:
    """Завернуть команду в sudo -S c подачей пароля на stdin."""
    # dbosroot уже sudoer; пароль читается через -S из stdin
    return f"echo '{password}' | sudo -S sh -c '{body}'"


def box_useradd(
    ssh_client,
    *,
    sudo_password: str,
    login: str,
    has_sudo: bool = False,
) -> None:
    """useradd на боксе. Идемпотентно — повторный вызов не падает."""
    body = f"useradd -m -s /bin/bash {login} 2>/dev/null || true"
    if has_sudo:
        # На linuxserver/openssh-server группа sudo называется именно `sudo`.
        body += f"; usermod -aG sudo {login}"
    rc, out, err = _exec(ssh_client, _sudo(sudo_password, body))
    assert rc == 0, f"useradd {login} failed rc={rc} out={out!r} err={err!r}"


def box_userdel(
    ssh_client, *, sudo_password: str, login: str,
) -> None:
    """userdel -r. Если юзера нет — не падаем (тест-setup может быть грязным)."""
    body = f"userdel -r {login} 2>/dev/null || true"
    rc, _, _ = _exec(ssh_client, _sudo(sudo_password, body))
    assert rc == 0


def box_set_sudo(
    ssh_client, *, sudo_password: str, login: str, has_sudo: bool,
) -> None:
    """Добавить/убрать sudo-членство у юзера."""
    if has_sudo:
        body = f"usermod -aG sudo {login}"
    else:
        body = f"gpasswd -d {login} sudo 2>/dev/null || true"
    rc, out, err = _exec(ssh_client, _sudo(sudo_password, body))
    assert rc == 0, f"set_sudo {login}={has_sudo} failed rc={rc} out={out!r} err={err!r}"


def box_getent_passwd(ssh_client, login: str) -> str | None:
    """`getent passwd login` для прямых ассертов из теста. None если нет."""
    rc, out, _ = _exec(ssh_client, f"getent passwd {login}")
    if rc != 0:
        return None
    return out.strip() or None


# ── Worker dispatch + task wait ─────────────────────────────────────────────


def dispatch_inventory_sync(
    server_client: httpx.Client, token: str, *, server_id: str,
) -> str:
    """POST /servers/{id}/inventory/sync → возвращает task_id."""
    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/inventory/sync",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, (
        f"inventory.sync dispatch failed: {r.status_code} {r.text}"
    )
    return r.json()["task_id"]


def dispatch_users_inventory(
    server_client: httpx.Client, token: str, *, server_id: str,
) -> str:
    """POST /servers/{id}/users/inventory → возвращает task_id."""
    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/users/inventory",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, (
        f"users.inventory dispatch failed: {r.status_code} {r.text}"
    )
    return r.json()["task_id"]


def wait_task_status(
    worker_db_engine,
    task_id: str,
    *,
    target: str = "succeeded",
    timeout_s: float = 60.0,
    poll_s: float = 0.5,
) -> dict:
    """Поллит worker-DB пока tasks.status не станет target (или failed).

    Возвращает full row (mappings). Падает по таймауту с last_error в сообщении.
    """
    deadline = time.monotonic() + timeout_s
    last_row: dict | None = None
    while time.monotonic() < deadline:
        with worker_db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, attempt, last_error, result "
                    "FROM tasks WHERE id = :id"
                ),
                {"id": task_id},
            ).mappings().first()
        if row is not None:
            last_row = dict(row)
            if row["status"] == target:
                return last_row
            if row["status"] == "failed" and target != "failed":
                raise AssertionError(
                    f"task {task_id} FAILED (expected {target}): "
                    f"last_error={row['last_error']!r}"
                )
        time.sleep(poll_s)
    raise AssertionError(
        f"task {task_id} did not reach status={target} within {timeout_s}s; "
        f"last_row={last_row!r}"
    )


# ── Audit queries (loging DB direct) ────────────────────────────────────────


def find_audit_event(
    loging_db_engine,
    *,
    action: str,
    server_id: str | None = None,
    severity: str | None = None,
    login: str | None = None,
    drift: str | None = None,
    timeout_s: float = 15.0,
    poll_s: float = 0.5,
) -> dict | None:
    """Прямой select по audit_events — нужно для матчинга по `details.login`/
    `details.drift`, чего нет в /api/logging/v1/events.

    Сортировка по timestamp DESC — берём свежайший, чтобы re-emit не подсунул
    нам исторический.
    """
    filters = ["action = :a"]
    params: dict = {"a": action}
    if severity is not None:
        filters.append("severity = :sev")
        params["sev"] = severity
    if server_id is not None:
        filters.append("(target_id = :sid OR details ->> 'server_id' = :sid)")
        params["sid"] = server_id
    if login is not None:
        filters.append("details ->> 'login' = :login")
        params["login"] = login
    if drift is not None:
        filters.append("details ->> 'drift' = :drift")
        params["drift"] = drift
    sql = (
        f"SELECT action, severity, status, target_id, target_type, details, timestamp "
        f"FROM audit_events WHERE {' AND '.join(filters)} "
        f"ORDER BY timestamp DESC LIMIT 1"
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with loging_db_engine.connect() as conn:
            row = conn.execute(text(sql), params).mappings().first()
        if row is not None:
            return dict(row)
        time.sleep(poll_s)
    return None


def assert_audit_event(
    loging_db_engine,
    **kwargs,
) -> dict:
    """find_audit_event + AssertionError при отсутствии."""
    ev = find_audit_event(loging_db_engine, **kwargs)
    assert ev is not None, (
        f"audit event not found: {kwargs!r}"
    )
    return ev


# ── DB-state probes (server_service direct) ────────────────────────────────


def get_server_row(server_db_engine, server_id: str) -> dict | None:
    """SELECT * FROM servers WHERE id = :id."""
    with server_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, hostname, cpu_brand, cpu_model, cpu_cores, cpu_threads, "
                "cpu_frequency_ghz, ram_total_mb, os_version_id, os_last_synced_at, "
                "is_managed FROM servers WHERE id = :id"
            ),
            {"id": server_id},
        ).mappings().first()
        return dict(row) if row else None


def get_os_version_row(server_db_engine, *, name: str | None = None, os_id: str | None = None) -> dict | None:
    """Lookup os_versions по name или id."""
    sql = "SELECT id, name FROM os_versions WHERE "
    params: dict = {}
    if name is not None:
        sql += "name = :name"
        params["name"] = name
    elif os_id is not None:
        sql += "id = :id"
        params["id"] = os_id
    else:
        raise ValueError("name or os_id required")
    with server_db_engine.connect() as conn:
        row = conn.execute(text(sql), params).mappings().first()
        return dict(row) if row else None


def get_account_row_by_login(
    server_db_engine, *, department_id: str, login: str,
) -> dict | None:
    """SELECT по логину в рамках dept."""
    with server_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, login, source, has_sudo, password_encrypted, "
                "department_id, unix_groups "
                "FROM server_accounts "
                "WHERE department_id = :dept AND login = :login "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"dept": department_id, "login": login},
        ).mappings().first()
        return dict(row) if row else None


def get_link_row(server_db_engine, *, account_id: str, server_id: str) -> dict | None:
    """SELECT из server_account_servers."""
    with server_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT account_id, server_id, login, present_on_server, "
                "last_inventory_at "
                "FROM server_account_servers "
                "WHERE account_id = :aid AND server_id = :sid"
            ),
            {"aid": account_id, "sid": server_id},
        ).mappings().first()
        return dict(row) if row else None
