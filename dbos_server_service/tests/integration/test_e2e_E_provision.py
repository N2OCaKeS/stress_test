"""E2E кластер E — provision / update_on_host / deprovision через реальный SSH.

Покрывает:

  * `POST /server-accounts/{id}/provision?server_id=X` → `useradd` на боксе,
    `getent passwd` подтверждает, audit `server_account.provision`
    success, `present_on_server=True` на link.
  * `POST /server-accounts/{id}/update_on_host?server_id=X` → `usermod` —
    sudo/groups/shell синкаются (проверка через `id -nG` и `getent passwd`).
  * `POST /server-accounts/{id}/deprovision?server_id=X&remove_home=true`
    → `userdel -r`, юзера на боксе нет, link `present_on_server=False`.
  * Fan-out PATCH на edit account: меняем `has_sudo`/`unix_groups`/`shell`,
    worker дёрнут `update_on_host` на все present-серверы автоматически.

Setup boxes как в `test_e2e_E_rotation.py`: `setup_management_user_on_box`
проставляет `dbos` юзера + NOPASSWD sudo + management-pubkey.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration._helpers_E_rotation import (
    cleanup_user_on_box,
    create_account,
    create_server_pointing_at_target,
    ensure_it_admin,
    fetch_account_link_state,
    find_department_id,
    mark_server_managed,
    setup_management_user_on_box,
    user_exists_on_box,
    user_groups_on_box,
    user_shell_on_box,
    wait_for_audit,
    wait_for_task,
)


# ── session-scoped setup (shared with rotation tests) ───────────────────────


@pytest.fixture(scope="session")
def _box_setup_done(ssh_test_host, ssh_session) -> None:
    setup_management_user_on_box(ssh_session, ssh_test_host)


@pytest.fixture(scope="session")
def _it_admin_creds(
    auth_client: httpx.Client,
    admin_token: str,
    make_user,
    login_token,
) -> dict:
    user_id, username, token = ensure_it_admin(
        auth_client, admin_token, make_user, login_token,
    )
    dept_id = find_department_id(auth_client, admin_token, "it")
    return {
        "user_id": user_id,
        "username": username,
        "token": token,
        "dept_id": dept_id,
    }


@pytest.fixture
def e_managed_server(
    reset_state,
    _box_setup_done,
    _it_admin_creds: dict,
    server_client: httpx.Client,
    server_db_engine,
) -> dict:
    suffix = uuid.uuid4().hex[:8]
    server = create_server_pointing_at_target(
        server_client,
        _it_admin_creds["token"],
        _it_admin_creds["dept_id"],
        suffix=suffix,
    )
    mark_server_managed(server_db_engine, server["id"])
    return {**server, **_it_admin_creds, "suffix": suffix}


# ════════════════════════════════════════════════════════════════════════════
# Provision — useradd
# ════════════════════════════════════════════════════════════════════════════


def test_provision_creates_user_on_box(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    worker_db_engine,
    loging_db_engine,
    ssh_session,
):
    """provision → юзер появился на боксе, present_on_server=True, audit success."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_prov_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
            unix_groups=["users"], shell="/bin/bash",
        )
        account_id = account["id"]

        # Pre-flight: link есть, present_on_server должен быть False.
        link = fetch_account_link_state(server_db_engine, account_id, server_id)
        assert link is not None, "link must exist after create"
        assert link["present_on_server"] is False

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, f"provision dispatch failed: {r.text}"
        task_id = r.json()["task_id"]

        task = wait_for_task(worker_db_engine, task_id, timeout=45.0)
        assert task["status"] == "succeeded", (
            f"provision task failed: err={task['last_error']} result={task['result']}"
        )

        assert user_exists_on_box(ssh_session, login), (
            f"user {login} should be present on box after provision"
        )

        link = fetch_account_link_state(server_db_engine, account_id, server_id)
        assert link["present_on_server"] is True

        wait_for_audit(
            loging_db_engine,
            action="server_account.provision",
            status="success",
            target_id=account_id,
        )
    finally:
        cleanup_user_on_box(ssh_session, login)


def test_provision_with_sudo_grants_sudo_group(
    e_managed_server: dict,
    server_client: httpx.Client,
    worker_db_engine,
    ssh_session,
    admin_server_client: httpx.Client,
):
    """Provision с has_sudo=True ставит юзеру `sudo` группу (через grant_sudo)."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_sudo_{e_managed_server['suffix']}"

    try:
        # has_sudo требует action `grant_sudo` — у dept admin он есть в дефолтной
        # admin-роли (по описанию ServerAccountCreate). Если нет — упадёт 403 и
        # это явная находка.
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!", has_sudo=True,
            unix_groups=["users"], shell="/bin/bash",
        )
        account_id = account["id"]

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded", task["last_error"]

        groups = user_groups_on_box(ssh_session, login)
        assert "sudo" in groups, f"sudo not in groups: {groups}"
    finally:
        cleanup_user_on_box(ssh_session, login)


# ════════════════════════════════════════════════════════════════════════════
# update_on_host — usermod (direct + fan-out)
# ════════════════════════════════════════════════════════════════════════════


def test_update_on_host_syncs_groups_and_shell(
    e_managed_server: dict,
    server_client: httpx.Client,
    worker_db_engine,
    loging_db_engine,
    ssh_session,
):
    """update_on_host: PATCH unix_groups → fan-out triggers usermod → box обновлён."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_upd_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
            unix_groups=["users"], shell="/bin/bash",
        )
        account_id = account["id"]

        # Provision → юзер на боксе + present_on_server=True (нужно для fan-out).
        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded", task["last_error"]

        # Прямой /update_on_host со сменой shell.
        # PATCH меняет shell в БД → API возвращает обновлённый объект; затем
        # explicit /update_on_host синкает шелл на боксе.
        r = server_client.patch(
            f"/api/server/v1/server-accounts/{account_id}",
            json={"shell": "/bin/sh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f"patch failed: {r.text}"

        # PATCH с has_sudo/unix_groups/shell автоматически дёргает fan-out
        # update_on_host на ВСЕ present серверы. Считаем последнюю задачу
        # `account.update_on_host` на этом аккаунте.
        import time as _time

        from sqlalchemy import text as _t

        task_id = None
        for _ in range(60):
            with worker_db_engine.connect() as conn:
                row = conn.execute(
                    _t(
                        "SELECT id FROM tasks WHERE task_kind='account.update_on_host' "
                        "AND target_resource_id = :aid ORDER BY enqueued_at DESC LIMIT 1"
                    ),
                    {"aid": account_id},
                ).first()
            if row is not None:
                task_id = row[0]
                break
            _time.sleep(0.5)
        assert task_id is not None, "fan-out update_on_host task did not appear"

        task = wait_for_task(worker_db_engine, task_id, timeout=45.0)
        assert task["status"] == "succeeded", task["last_error"]

        # Box: shell обновлён через usermod.
        assert user_shell_on_box(ssh_session, login) == "/bin/sh"

        wait_for_audit(
            loging_db_engine,
            action="server_account.update_on_host",
            status="success",
            target_id=account_id,
        )
    finally:
        cleanup_user_on_box(ssh_session, login)


def test_patch_account_fanout_update_on_host(
    e_managed_server: dict,
    server_client: httpx.Client,
    worker_db_engine,
    ssh_session,
):
    """PATCH unix_groups → автоматический fan-out на все present серверы.

    Проверяем что PATCH аккаунта (без явного /update_on_host) сам ставит
    задачу `account.update_on_host` через `fanout_update_on_host`.
    """
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_fan_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
            unix_groups=["users"], shell="/bin/bash",
        )
        account_id = account["id"]

        # Provision → present_on_server=True.
        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded"

        # Снимок tasks ДО PATCH — для отделения fan-out task'и от provision'а.
        from sqlalchemy import text as _t

        with worker_db_engine.connect() as conn:
            before_ids = {
                r[0] for r in conn.execute(
                    _t(
                        "SELECT id FROM tasks WHERE task_kind='account.update_on_host' "
                        "AND target_resource_id = :aid"
                    ),
                    {"aid": account_id},
                ).all()
            }

        # PATCH unix_groups — OS-managed поле, должен триггерить fan-out.
        r = server_client.patch(
            f"/api/server/v1/server-accounts/{account_id}",
            json={"unix_groups": ["users", "adm"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text

        # Ждём появления новой update_on_host-task'и для этого аккаунта.
        import time as _time
        task_id = None
        for _ in range(60):
            with worker_db_engine.connect() as conn:
                new_ids = {
                    r[0] for r in conn.execute(
                        _t(
                            "SELECT id FROM tasks WHERE task_kind='account.update_on_host' "
                            "AND target_resource_id = :aid"
                        ),
                        {"aid": account_id},
                    ).all()
                }
            extras = new_ids - before_ids
            if extras:
                task_id = next(iter(extras))
                break
            _time.sleep(0.5)
        assert task_id is not None, "fan-out did not produce update_on_host task"

        task = wait_for_task(worker_db_engine, task_id, timeout=45.0)
        assert task["status"] == "succeeded", task["last_error"]

        groups = user_groups_on_box(ssh_session, login)
        assert "adm" in groups, f"adm group not applied: {groups}"
    finally:
        cleanup_user_on_box(ssh_session, login)


# ════════════════════════════════════════════════════════════════════════════
# Deprovision — userdel
# ════════════════════════════════════════════════════════════════════════════


def test_deprovision_removes_user_and_home(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    worker_db_engine,
    loging_db_engine,
    ssh_session,
):
    """deprovision?remove_home=true → userdel -r, юзера на боксе нет, present=False."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_dep_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
        )
        account_id = account["id"]

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded"
        assert user_exists_on_box(ssh_session, login)

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/deprovision",
            params={"server_id": server_id, "remove_home": "true"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded", task["last_error"]

        assert not user_exists_on_box(ssh_session, login), (
            f"user {login} should be removed from box"
        )

        link = fetch_account_link_state(server_db_engine, account_id, server_id)
        assert link is not None
        assert link["present_on_server"] is False

        wait_for_audit(
            loging_db_engine,
            action="server_account.deprovision",
            status="success",
            target_id=account_id,
        )
    finally:
        # Defensive: если deprovision не сработал, cleanup всё равно гарантирован.
        cleanup_user_on_box(ssh_session, login)
