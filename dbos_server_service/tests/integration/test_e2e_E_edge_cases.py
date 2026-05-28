"""E2E кластер E — edge-кейсы provision/inventory на managed-сервере.

Покрывает:

  * Discovered-аккаунт (без `password_encrypted`) → provision на managed
    проходит SUCCEEDED, chpasswd skipped (INFO-лог, не failure).
  * `installed_packages.list` task на managed-сервере не вызывает
    `fetch_account_password` для аккаунта в payload — сессия идёт под
    management-юзером по ключу.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from sqlalchemy import text

from tests.integration._helpers_E_rotation import (
    cleanup_user_on_box,
    clear_account_password,
    create_account,
    create_server_pointing_at_target,
    ensure_it_admin,
    find_department_id,
    mark_server_managed,
    setup_management_user_on_box,
    user_exists_on_box,
    wait_for_audit,
    wait_for_task,
)


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
        "user_id": user_id, "username": username,
        "token": token, "dept_id": dept_id,
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
# Discovered-аккаунт (без password)
# ════════════════════════════════════════════════════════════════════════════


def test_provision_discovered_account_without_password_succeeds(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    worker_db_engine,
    ssh_session,
):
    """Discovered-аккаунт (password_encrypted=NULL) provision на managed → SUCCESS.

    На управляемом сервере воркер пытается тянуть пароль best-effort через
    `_fetch_password_to_set`; если server_service вернул
    `ACCOUNT_PASSWORD_UNAVAILABLE`, шаг chpasswd пропускается без падения
    task'и. Юзер на боксе создаётся (`useradd` без `-p`).
    """
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_disc_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
        )
        account_id = account["id"]

        # Сбрасываем хранимый пароль — имитируем discovered-аккаунт.
        clear_account_password(server_db_engine, account_id)

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        task = wait_for_task(worker_db_engine, r.json()["task_id"], timeout=45.0)
        assert task["status"] == "succeeded", (
            f"discovered-acct provision should succeed; "
            f"status={task['status']} err={task['last_error']}"
        )
        assert user_exists_on_box(ssh_session, login), (
            f"user {login} should exist on box even without stored password"
        )
    finally:
        cleanup_user_on_box(ssh_session, login)


# ════════════════════════════════════════════════════════════════════════════
# installed_packages.list — на managed-сервере без fetch пароля
# ════════════════════════════════════════════════════════════════════════════


def test_installed_packages_list_on_managed_server(
    e_managed_server: dict,
    server_client: httpx.Client,
    worker_db_engine,
    loging_db_engine,
):
    """installed_packages.list на managed → задача успешна, dpkg/rpm результат непустой.

    Endpoint `GET /servers/{id}/installed-packages` НЕ кладёт `account_id` в
    payload (только `server_id`, `pattern`, `target_department_id`). Worker
    видит `is_managed=True` (если payload приходит с этим флагом) и сессию
    строит под management-юзером по ключу — fetch пароля не делается.

    Текущая реализация `server_service/.../installed_packages.py` не добавляет
    в payload `is_managed`/`management_user`/`ssh_host`, поэтому worker
    свалится на self-сессию без логина. Тест ловит именно эту разницу.
    """
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]

    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/installed-packages",
        params={"pattern": "bash"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, f"installed_packages dispatch failed: {r.text}"
    task_id = r.json()["task_id"]

    task = wait_for_task(worker_db_engine, task_id, timeout=45.0)
    assert task["status"] == "succeeded", (
        f"installed_packages task failed on managed server: "
        f"err={task['last_error']}"
    )

    result = task["result"] or {}
    assert result.get("server_id") == server_id
    assert result.get("package_manager") in ("dpkg", "rpm"), (
        f"unexpected package_manager: {result}"
    )
    assert result.get("count", 0) >= 1, f"expected ≥1 bash package: {result}"

    # Доп.проверка: audit-row для `installed_packages.list` есть с success.
    wait_for_audit(
        loging_db_engine,
        action="installed_packages.list",
        status="success",
        target_id=server_id,
    )


def test_installed_packages_no_password_fetch_on_managed(
    e_managed_server: dict,
    server_client: httpx.Client,
    loging_db_engine,
    worker_db_engine,
):
    """На managed-сервере при installed_packages не должно быть audit'а
    `server_account.view_password` (worker ходит по ключу, fetch не делает).

    Берём timestamp ДО dispatch'а и проверяем, что за время выполнения
    task'и не появилось `view_password` событий с success.
    """
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]

    # Снимок «сколько view_password событий было до».
    with loging_db_engine.connect() as conn:
        before_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE action = 'server_account.view_password' AND status='success'"
            ),
        ).scalar() or 0

    r = server_client.post(
        f"/api/server/v1/servers/{server_id}/installed-packages",
        params={"pattern": "*"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    task = wait_for_task(worker_db_engine, task_id, timeout=60.0)
    # Не обязательно требуем success — гарантия не в этом; гарантия в том,
    # что view_password не вызван. Однако если task упала из-за SSH —
    # покажем причину в assert message.
    if task["status"] != "succeeded":
        # Падение не обязательно делает тест плохим: ниже всё равно проверим
        # отсутствие fetch-вызова. Но фиксируем как warning через print —
        # отчёт перечислит в "Tech debt".
        print(
            f"[warn] installed_packages task ended {task['status']}: "
            f"{task['last_error']}"
        )

    # Дать audit'у время дозаписаться.
    time.sleep(2.0)

    with loging_db_engine.connect() as conn:
        after_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE action = 'server_account.view_password' AND status='success'"
            ),
        ).scalar() or 0

    assert after_count == before_count, (
        "managed-сервер installed_packages.list НЕ должен вызывать "
        "fetch_account_password (а значит и view_password audit); "
        f"events before={before_count} after={after_count}"
    )
