"""Helpers for cluster E (mass rotation + provision/update/deprovision).

Shared utilities for `test_e2e_E_*.py`. Кластер E работает с реальным
openssh-target: ставит / меняет / снимает OS-пользователя, ротирует пароль
через worker по SSH. Перед сценариями надо:

  * подготовить openssh-target к роли managed-сервера (юзер `dbos` с
    NOPASSWD-sudo и authorized_keys, в которые положен management-pubkey);
  * зарегистрировать в server_service сервер с `hostname=ssh-target`,
    чтобы worker мог попасть на бокс по docker-network DNS;
  * иметь юзера в department `it` с service-ролью `admin` для
    `server_service` (admin-token от платформенного `account_admin`
    отбивается middleware'ом — он не может работать с бизнес-данными).

Хелперы тут — общие для E/F/G. F (prepare) поднимет свой полный pipeline
бутстрапа через endpoint; для E нам достаточно sidestep-варианта
«подготовить openssh-target вручную + проставить server.is_managed в БД»,
чтобы тестировать именно rotate/provision/update/deprovision flow без
зависимости от prepare worker'а.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
import paramiko
from sqlalchemy import text


# ════════════════════════════════════════════════════════════════════════════
# IT-department admin (бизнес-юзер, не account_admin)
# ════════════════════════════════════════════════════════════════════════════
#
# Платформенный account_admin отбивается middleware'ом на любом business-
# endpoint (PLATFORM_ADMIN_BUSINESS_DATA_DENIED). Для CRUD серверов и
# server-accounts нужен юзер department-уровня. Берём dept `it` (его сеет
# `seed_worker_pat.py`), создаём юзера и выдаём ему service-role `admin` для
# `server_service`.


def find_department_id(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Найти id отдела по имени. Бросает, если отдел отсутствует."""
    r = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, f"list departments failed: {r.text}"
    data = r.json()
    items = data.get("items") if isinstance(data, dict) else data
    for d in items or []:
        if d.get("name") == name:
            return d.get("department_id") or d["id"]
    raise AssertionError(f"department {name!r} not found")


def ensure_it_admin(
    auth_client: httpx.Client,
    admin_token: str,
    make_user,
    login_token,
) -> tuple[str, str, str]:
    """Создать юзера в `it` с service-role `admin` на server_service.

    Идемпотентность: один раз на сессию (caller сам кэширует).
    Возвращает: `(user_id, username, access_token)`.
    """
    dept_id = find_department_id(auth_client, admin_token, "it")
    user = make_user(
        password="ItAdmin1!",
        department_id=dept_id,
    )
    user_id = user.get("id") or user.get("user_id")
    assert user_id, f"make_user response missing id: {user}"

    # Bulk-assign `admin` роли через /departments/{id}/services/{svc}/roles/{role}/assign
    r = auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services/server_service/roles/admin/assign",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_id]},
    )
    assert r.status_code in (200, 201), (
        f"assign admin role failed: {r.status_code} {r.text}"
    )

    token = login_token(user["username"], "ItAdmin1!")
    return user_id, user["username"], token


# ════════════════════════════════════════════════════════════════════════════
# openssh-target management-setup
# ════════════════════════════════════════════════════════════════════════════


def _exec(client: paramiko.SSHClient, cmd: str, *, expect_rc: int | None = 0) -> tuple[int, str, str]:
    """Выполнить команду на удалённом хосте. Sudo идёт под `dbosroot` (NOPASSWD)."""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if expect_rc is not None:
        assert rc == expect_rc, (
            f"cmd {cmd!r} rc={rc} expected={expect_rc}\nstdout: {out}\nstderr: {err}"
        )
    return rc, out, err


def setup_management_user_on_box(ssh_session, ssh_test_host: dict) -> None:
    """Идемпотентно подготовить openssh-target как managed-сервер.

    Что делаем (под рутовым bootstrap-аккаунтом):
      1. useradd dbos (если нет) с shell `/bin/bash` и home `/home/dbos`.
      2. NOPASSWD sudo через `/etc/sudoers.d/dbos-management` (visudo-check).
      3. `~/.ssh/authorized_keys` с management-pubkey'ём (без дублей).

    После этого сервер ready для test'ов E: можно ходить ключом под `dbos`
    с sudo, что и делает worker под `is_managed=True`.
    """
    mgmt_user = ssh_test_host["mgmt_user"]
    pubkey = ssh_test_host["mgmt_pubkey"]
    assert pubkey, "mgmt_pubkey must be loaded (init-ssh-keys ran)"

    with ssh_session(as_mgmt=False) as ssh:
        # `getent passwd` для проверки существования юзера. rc=0 — есть.
        rc, _, _ = _exec(ssh, f"getent passwd {mgmt_user}", expect_rc=None)
        if rc != 0:
            _exec(
                ssh,
                f"sudo useradd -m -s /bin/bash {mgmt_user}",
            )

        # sudoers-фрагмент через tee, потом visudo-check.
        sudoers_line = f"{mgmt_user} ALL=(ALL) NOPASSWD: ALL"
        sudoers_path = f"/etc/sudoers.d/{mgmt_user}-management"
        _exec(
            ssh,
            f"echo '{sudoers_line}' | sudo tee {sudoers_path} > /dev/null "
            f"&& sudo chmod 0440 {sudoers_path} "
            f"&& sudo visudo -c -f {sudoers_path}",
        )

        # ~/.ssh + authorized_keys. mkdir -p идемпотентно; key через grep+append.
        home = f"/home/{mgmt_user}"
        _exec(ssh, f"sudo mkdir -p {home}/.ssh && sudo chmod 700 {home}/.ssh")
        # Pubkey содержит пробелы; пишем через base64 чтобы не возиться с
        # экранированием в shell.
        import base64

        b64 = base64.b64encode(pubkey.encode("utf-8")).decode("ascii")
        _exec(
            ssh,
            (
                f"KEY=$(echo {b64} | base64 -d); "
                f"sudo touch {home}/.ssh/authorized_keys && "
                f"sudo chmod 600 {home}/.ssh/authorized_keys && "
                f"sudo chown -R {mgmt_user}:{mgmt_user} {home}/.ssh && "
                f"sudo grep -qxF \"$KEY\" {home}/.ssh/authorized_keys || "
                f"echo \"$KEY\" | sudo tee -a {home}/.ssh/authorized_keys > /dev/null"
            ),
        )


def assert_can_login_as_mgmt(ssh_session) -> None:
    """Sanity: после setup'а management-юзер ходит ключом с sudo."""
    with ssh_session(as_mgmt=True) as ssh:
        _, out, _ = _exec(ssh, "whoami")
        assert out.strip() == "dbos", f"expected whoami=dbos, got {out!r}"
        _, out, _ = _exec(ssh, "sudo -n whoami")
        assert out.strip() == "root", f"expected sudo whoami=root, got {out!r}"


def user_exists_on_box(ssh_session, login: str) -> bool:
    """`getent passwd <login>` → True если юзер на боксе есть."""
    with ssh_session(as_mgmt=True) as ssh:
        rc, _, _ = _exec(ssh, f"getent passwd {login}", expect_rc=None)
    return rc == 0


def user_groups_on_box(ssh_session, login: str) -> list[str]:
    """`id -nG <login>` → плоский список групп юзера."""
    with ssh_session(as_mgmt=True) as ssh:
        rc, out, err = _exec(ssh, f"id -nG {login}", expect_rc=None)
    if rc != 0:
        raise AssertionError(f"id -nG {login} failed: {err}")
    return out.strip().split()


def user_shell_on_box(ssh_session, login: str) -> str:
    """Из `getent passwd <login>` достать поле shell (7-я колонка)."""
    with ssh_session(as_mgmt=True) as ssh:
        rc, out, _ = _exec(ssh, f"getent passwd {login}", expect_rc=None)
    assert rc == 0, f"user {login} not on box"
    return out.strip().split(":")[-1]


def user_password_works(ssh_test_host: dict, login: str, password: str) -> bool:
    """Попробовать SSH под `login`+`password` (не управляющий ключ).

    Возвращает True, если password authentication прошла и команда отдала
    rc=0. Используется для проверки результата ротации пароля.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ssh_test_host["host"],
            port=ssh_test_host["port"],
            username=login,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=8,
            auth_timeout=8,
            banner_timeout=8,
        )
    except (paramiko.AuthenticationException, paramiko.SSHException, OSError):
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass
    return True


# ════════════════════════════════════════════════════════════════════════════
# server_service-level helpers
# ════════════════════════════════════════════════════════════════════════════


def create_server_pointing_at_target(
    server_client: httpx.Client,
    token: str,
    department_id: str,
    *,
    suffix: str,
    hostname_override: str | None = None,
) -> dict:
    """POST /servers с hostname=`ssh-target` (docker DNS) и уникальным IP/serial.

    `ssh-target` в docker-сети резолвится через embedded DNS — worker'у этого
    достаточно, чтобы попасть в openssh-server-контейнер по 2222. IP в БД
    нужен только для уникальности (worker его не использует для SSH).

    `suffix` уникализирует hostname/ip/serial между тестами.
    """
    # ip_address — INET, должен быть уникален. Берём 10.99.X.Y, где X.Y
    # уникальны через hash(suffix).
    h = abs(hash(suffix)) % 65535
    ip = f"10.99.{h // 256}.{h % 256}"
    body = {
        "hostname": hostname_override or f"ssh-target-{suffix}",
        "display_name": f"E-test {suffix}",
        "ip_address": ip,
        "ssh_port": 2222,
        "department_id": department_id,
        "serial_number": f"E-SN-{suffix}",
    }
    r = server_client.post(
        "/api/server/v1/servers",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, f"create server failed: {r.status_code} {r.text}"
    return r.json()


def mark_server_managed(server_db_engine, server_id: str, management_user: str = "dbos") -> None:
    """Прокинуть `is_managed=True` + `management_user` напрямую в БД.

    Sidesteps `POST /servers/{id}/prepare` (это flow ловит кластер F). Для
    E нам нужно лишь, чтобы worker строил management-сессию (ключ под dbos
    с sudo), что включается флагом `server.is_managed`.
    """
    with server_db_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE servers SET is_managed = TRUE, management_user = :u, "
                "prepared_at = NOW() WHERE id = :sid"
            ),
            {"u": management_user, "sid": server_id},
        )


def set_server_hostname_to_ssh_target(server_db_engine, server_id: str) -> None:
    """Поставить server.hostname = 'ssh-target' напрямую.

    Так как UNIQUE-constraint на hostname блокирует создавать несколько
    серверов с одинаковым именем через API, у каждого тестового сервера
    суффикс. Но worker, к сожалению, не использует hostname/ip_address —
    он SSH-ится на `server_id` (см. ssh_client._extract_host fallback). Без
    патча worker'а / dispatch payload'а этот хелпер сам по себе НЕ решает
    проблему резолва; оставлен для случая, если worker станет уважать
    server.hostname через какой-нибудь будущий ssh_host-проброс.
    """
    with server_db_engine.begin() as conn:
        conn.execute(
            text("UPDATE servers SET hostname = 'ssh-target' WHERE id = :sid"),
            {"sid": server_id},
        )


def create_account(
    server_client: httpx.Client,
    token: str,
    server_id: str,
    *,
    login: str,
    password: str | None = "InitPass1!",
    has_sudo: bool = False,
    unix_groups: list[str] | None = None,
    shell: str | None = None,
) -> dict:
    """POST /server-accounts. Если password=None — сервер сгенерит сам."""
    body: dict = {
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
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, f"create account failed: {r.status_code} {r.text}"
    return r.json()


def clear_account_password(server_db_engine, account_id: str) -> None:
    """Симулировать discovered-аккаунт: сбросить `password_encrypted` в NULL.

    Создание через API всегда даёт пароль (генерится, если не передан); для
    кейса «discovered-аккаунт без хранимого пароля» обнуляем поле напрямую.
    """
    with server_db_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE server_accounts SET password_encrypted = NULL, "
                "source = 'discovered' WHERE id = :aid"
            ),
            {"aid": account_id},
        )


def link_servers(
    server_client: httpx.Client,
    token: str,
    account_id: str,
    server_ids: list[str],
) -> dict:
    """POST /server-accounts/{id}/servers — линковка дополнительных серверов."""
    r = server_client.post(
        f"/api/server/v1/server-accounts/{account_id}/servers",
        json={"server_ids": server_ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"link servers failed: {r.status_code} {r.text}"
    return r.json()


def decommission_server(server_db_engine, server_id: str) -> None:
    """Перевести сервер в status=decommissioned напрямую (через БД)."""
    with server_db_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE servers SET status = 'decommissioned', "
                "decommissioned_at = NOW() WHERE id = :sid"
            ),
            {"sid": server_id},
        )


# ════════════════════════════════════════════════════════════════════════════
# Task / audit polling
# ════════════════════════════════════════════════════════════════════════════


def wait_for_task(
    worker_db_engine,
    task_id: str,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> dict:
    """Ждать терминального статуса task'и (succeeded / failed / cancelled).

    Возвращает row task'и (mapping). Таймаут → AssertionError со снэпшотом
    последнего status/last_error/attempt.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        with worker_db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, attempt, last_error, result, started_at, "
                    "completed_at, task_kind FROM tasks WHERE id = :tid"
                ),
                {"tid": task_id},
            ).mappings().first()
        if row is not None:
            last = dict(row)
            if row["status"] in ("succeeded", "failed", "cancelled"):
                return last
        time.sleep(poll_interval)
    raise AssertionError(
        f"task {task_id} did not reach terminal status within {timeout}s; "
        f"last snapshot: {last}"
    )


def wait_for_task_running_or_done(
    worker_db_engine,
    task_id: str,
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.3,
) -> dict:
    """Ждать первого перехода в running/succeeded/failed (что-то началось)."""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        with worker_db_engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, status, attempt FROM tasks WHERE id = :tid"),
                {"tid": task_id},
            ).mappings().first()
        if row is not None:
            last = dict(row)
            if row["status"] != "queued":
                return last
        time.sleep(poll_interval)
    raise AssertionError(
        f"task {task_id} stayed queued for {timeout}s; last: {last}"
    )


def query_audit_events(
    loging_db_engine,
    *,
    action: str,
    status: str | None = None,
    target_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """SELECT из `audit_events` напрямую — нужнее, чем GET /events, когда
    надо проверить поля details JSONB.
    """
    sql = "SELECT action, status, severity, target_id, actor_id, details, timestamp FROM audit_events WHERE action = :a"
    params: dict = {"a": action}
    if status is not None:
        sql += " AND status = :s"
        params["s"] = status
    if target_id is not None:
        sql += " AND target_id = :t"
        params["t"] = target_id
    sql += " ORDER BY timestamp DESC LIMIT :lim"
    params["lim"] = limit
    with loging_db_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def wait_for_audit(
    loging_db_engine,
    *,
    action: str,
    status: str | None = None,
    target_id: str | None = None,
    timeout: float = 10.0,
    poll_interval: float = 0.3,
) -> dict:
    """Polling-ожидание audit-события (audit-emit async)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = query_audit_events(
            loging_db_engine,
            action=action, status=status, target_id=target_id, limit=1,
        )
        if rows:
            return rows[0]
        time.sleep(poll_interval)
    raise AssertionError(
        f"audit event action={action!r} status={status!r} target={target_id!r} "
        f"not found within {timeout}s"
    )


def fetch_account_link_state(
    server_db_engine, account_id: str, server_id: str,
) -> dict | None:
    """SELECT строки M2M `server_accounts_servers` (account ↔ server link)."""
    with server_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT account_id, server_id, present_on_server "
                "FROM server_account_servers "
                "WHERE account_id = :aid AND server_id = :sid"
            ),
            {"aid": account_id, "sid": server_id},
        ).mappings().first()
    return dict(row) if row else None


def fetch_account_ciphertext(server_db_engine, account_id: str) -> bytes | None:
    """Достать сырой `password_encrypted` (для сравнения «изменился ли»)."""
    with server_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT password_encrypted FROM server_accounts WHERE id = :aid"
            ),
            {"aid": account_id},
        ).first()
    if row is None:
        return None
    return row[0]


# ════════════════════════════════════════════════════════════════════════════
# Cleanup helpers — удаление OS-юзера с openssh-target (между тестами `reset_state`
# чистит БД, но юзеры на боксе остаются и копятся между прогонами)
# ════════════════════════════════════════════════════════════════════════════


def cleanup_user_on_box(ssh_session, login: str) -> None:
    """`userdel -r` под mgmt-ключом. Не падает, если юзера нет."""
    with ssh_session(as_mgmt=True) as ssh:
        # `userdel -r` удаляет и home. rc=6 — юзера нет, это ок.
        _exec(ssh, f"sudo userdel -r {login} 2>/dev/null || true", expect_rc=None)


@dataclass
class ETestContext:
    """Контекст, переиспользуемый между сценариями E.

    Создаётся фикстурой `e_context` (per-test, под reset_state). Несёт:
      * department_id отдела `it`;
      * token+user_id бизнес-админа;
      * mgmt setup на боксе уже выполнен (session-cached);
      * server_id зарегистрированного managed-сервера, готового к
        provision/rotate.
    """

    dept_id: str
    admin_user_id: str
    admin_username: str
    admin_token: str
    server_id: str
    server_hostname: str
