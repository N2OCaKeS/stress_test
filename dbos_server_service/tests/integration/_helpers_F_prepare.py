"""Helpers для test_e2e_F_prepare.

Сценарий F работает поверх живого compose-стека (см. `docker-compose.test.yml`):
seeder уже создал department `it`. Здесь мы поверх этого делаем:

* dept-isolated user'а с ролью `admin` (системная роль server_service —
  даёт все CRUD-действия, включая `update` для prepare-dispatch);
* регистрацию сервера в server_service с `hostname=ssh-target` — чтобы
  worker мог достучаться до openssh-target по DNS внутри compose-сети;
* поллинги worker-task'и и Redis-ключа bootstrap-кред.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
from sqlalchemy import text


SERVER_SERVICE_NAME = "server_service"
IT_DEPT_NAME = "it"


def b64(value: str) -> str:
    """UTF-8 → base64 без переноса. Дублирующая копия server_service-кодека —
    тестам она нужна только в одну сторону."""
    return base64.b64encode(value.encode()).decode()


def _u() -> str:
    return uuid.uuid4().hex[:8]


# ── Setup department/users ────────────────────────────────────────────────────

def find_department_id(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Найти department по имени. seeder уже создал `it`."""
    r = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r.raise_for_status()
    items = r.json().get("items") or r.json()
    if isinstance(items, dict):
        items = items.get("items") or []
    for d in items:
        if d.get("name") == name:
            return d.get("department_id") or d["id"]
    raise AssertionError(f"department {name!r} not found")


def ensure_department(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Создать department, если не существует."""
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name.title()},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    return find_department_id(auth_client, admin_token, name)


def ensure_server_service_grant(
    auth_client: httpx.Client, admin_token: str, dept_id: str,
) -> None:
    """Выдать departmenту доступ к server_service (если ещё не выдан)."""
    r = auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": SERVER_SERVICE_NAME},
    )
    # 201 — выдан, 409 — уже был, 200 — fallback.
    assert r.status_code in (200, 201, 409), f"grant failed: {r.text}"


def make_admin_in_dept(
    auth_client: httpx.Client,
    admin_token: str,
    dept_id: str,
    *,
    username: str | None = None,
    password: str = "PrepUser1234!",
) -> dict:
    """Создать юзера с системной ролью `admin` в server_service департамента.

    Системная роль `admin` сеется автоматически миграцией при выдаче
    department'у доступа к сервису, даёт все CRUD-действия (включая `update`
    — необходимое право для `/servers/{id}/prepare`).
    """
    uname = username or f"prep_admin_{_u()}"
    r = auth_client.post(
        "/api/auth/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": uname,
            "password": password,
            "department_id": dept_id,
            "initial_roles": [
                {"service_name": SERVER_SERVICE_NAME, "roles": ["admin"]},
            ],
        },
    )
    assert r.status_code in (200, 201), f"make_admin_in_dept: {r.status_code} {r.text}"
    data = r.json()
    data["_password"] = password
    data["_username"] = uname
    return data


# ── Server creation ───────────────────────────────────────────────────────────

def create_test_server(
    server_client: httpx.Client,
    token: str,
    *,
    dept_id: str,
    hostname: str = "ssh-target",
    ip_address: str = "10.99.0.10",
    ssh_port: int = 2222,
) -> dict:
    """Зарегистрировать сервер с hostname=ssh-target.

    Уникальные suffix'ы добавляем к hostname/ip/serial — между тестами
    state не зачищается (см. `reset_state` для зачистки, если нужно).
    """
    u = _u()
    r = server_client.post(
        "/api/server/v1/servers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hostname": f"{hostname}-{u}",
            "ip_address": ip_address,
            "ssh_port": ssh_port,
            "department_id": dept_id,
        },
    )
    assert r.status_code == 201, f"create_test_server failed: {r.status_code} {r.text}"
    return r.json()


# ── Task polling ──────────────────────────────────────────────────────────────

def wait_task_status(
    worker_db_engine,
    task_id: str,
    *,
    target_statuses: tuple[str, ...] = ("succeeded", "failed"),
    retries: int = 60,
    delay: float = 0.5,
) -> dict:
    """Поллит `tasks` в worker_db_test до достижения терминального статуса.

    Возвращает row как mapping. Поднимает AssertionError по таймауту.
    """
    for _ in range(retries):
        with worker_db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, attempts, last_error, payload, "
                    "       task_kind, target_server_id, result "
                    "FROM tasks WHERE id = :tid"
                ),
                {"tid": task_id},
            ).mappings().first()
        if row is not None and row["status"] in target_statuses:
            return dict(row)
        time.sleep(delay)
    raise AssertionError(
        f"task {task_id} did not reach {target_statuses} in {retries * delay:.1f}s "
        f"(last={dict(row) if row else None})"
    )


def fetch_task(worker_db_engine, task_id: str) -> dict | None:
    with worker_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, status, attempts, payload, last_error, result "
                "FROM tasks WHERE id = :tid"
            ),
            {"tid": task_id},
        ).mappings().first()
    return dict(row) if row else None


# ── Redis helpers ─────────────────────────────────────────────────────────────

def redis_client(url: str):
    import redis
    return redis.from_url(url, decode_responses=True)


def wait_redis_key_present(
    rds, key: str, *, retries: int = 30, delay: float = 0.1,
) -> bool:
    """Поллит EXISTS, пока ключ не появится. False — если так и не появился."""
    for _ in range(retries):
        if rds.exists(key):
            return True
        time.sleep(delay)
    return False


def wait_redis_key_gone(
    rds, key: str, *, retries: int = 40, delay: float = 0.25,
) -> bool:
    """Поллит EXISTS, пока ключ не исчезнет."""
    for _ in range(retries):
        if not rds.exists(key):
            return True
        time.sleep(delay)
    return False


# ── Audit helpers ─────────────────────────────────────────────────────────────

def fetch_audit_events(
    loging_db_engine,
    *,
    action: str,
    target_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Прямой SELECT по audit_events: нужно когда поллинг /events гонит race
    с filter'ами по details (JSONB)."""
    where = ["action = :action"]
    params: dict = {"action": action}
    if target_id is not None:
        where.append("target_id = :target_id")
        params["target_id"] = target_id
    if status is not None:
        where.append("status = :status")
        params["status"] = status
    sql = (
        "SELECT id, action, status, severity, allowed, actor_id, target_id, "
        "       target_type, service, department_id, details, timestamp "
        f"FROM audit_events WHERE {' AND '.join(where)} "
        "ORDER BY timestamp DESC LIMIT :lim"
    )
    params["lim"] = limit
    with loging_db_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), str):
            try:
                d["details"] = json.loads(d["details"])
            except json.JSONDecodeError:
                pass
        out.append(d)
    return out


def wait_audit_event(
    loging_db_engine,
    *,
    action: str,
    target_id: str | None = None,
    status: str | None = None,
    retries: int = 30,
    delay: float = 0.5,
) -> dict:
    """Поллит audit_events, пока не появится подходящая строка."""
    for _ in range(retries):
        events = fetch_audit_events(
            loging_db_engine, action=action, target_id=target_id, status=status,
        )
        if events:
            return events[0]
        time.sleep(delay)
    raise AssertionError(
        f"audit event action={action!r} target_id={target_id!r} status={status!r} "
        f"not found after {retries * delay:.1f}s"
    )


# ── SSH bootstrap-target prep ─────────────────────────────────────────────────

def ensure_dbos_user_absent(ssh_session, mgmt_user: str = "dbos") -> None:
    """Перед idempotency-сценариями полезно прибрать пользователя `dbos` с
    бокса (linuxserver/openssh-server не персистит home по умолчанию, но
    между тестами в рамках одной compose-сессии остатки могут жить).

    Best-effort: ошибки игнорируем."""
    with ssh_session() as ssh:
        # userdel может вернуть ненулевой код, если юзера нет — это ок.
        ssh.exec_command("sudo -n userdel -rf dbos 2>/dev/null || true")
        ssh.exec_command("sudo -n rm -f /etc/sudoers.d/dbos-management 2>/dev/null || true")


def read_authorized_keys(ssh_session, mgmt_user: str = "dbos") -> str:
    """Прочитать `~mgmt_user/.ssh/authorized_keys` (как root)."""
    with ssh_session() as ssh:
        _, stdout, _ = ssh.exec_command(
            f"sudo -n cat /home/{mgmt_user}/.ssh/authorized_keys 2>/dev/null || true"
        )
        return stdout.read().decode()


def check_sudoers_valid(ssh_session, sudoers_path: str = "/etc/sudoers.d/dbos-management") -> tuple[int, str]:
    """Запустить `visudo -cf` на sudoers-файле. Возвращает (exit_code, stderr)."""
    with ssh_session() as ssh:
        _, stdout, stderr = ssh.exec_command(f"sudo -n visudo -cf {sudoers_path}")
        out = stdout.read().decode()
        err = stderr.read().decode()
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, out + err


def read_sudoers_file(ssh_session, sudoers_path: str = "/etc/sudoers.d/dbos-management") -> str:
    """Прочитать содержимое sudoers-файла под root'ом."""
    with ssh_session() as ssh:
        _, stdout, _ = ssh.exec_command(f"sudo -n cat {sudoers_path} 2>/dev/null || true")
        return stdout.read().decode()
