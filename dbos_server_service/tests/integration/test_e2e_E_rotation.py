"""E2E кластер E — ротация паролей (single / mass) через реальный SSH worker.

Покрывает:

  * `POST /server-accounts/{id}/rotate_password?server_id=X` — точечная
    ротация на один привязанный сервер; worker SSH+chpasswd + submit
    ciphertext; ciphertext в server_service обновлён; CRITICAL audit
    `server_account.password_rotate` со status=success.
  * `POST /server-accounts/{id}/rotate` (без server_id) — массовая
    ротация на все привязанные сервера; каждый сервер — независимая task'а;
    aggregated audit `server_account.rotate_password_dispatch` с
    `dispatched`/`skipped`.
  * Один decommissioned сервер в массовой ротации не валит батч.
  * Все decommissioned → 409 SERVER_DECOMMISSIONED.

Все тесты ходят через `ssh_session` фикстуру для проверки состояния
openssh-target (реальная коробка). Для setup'а management-юзера на боксе
используется `_helpers_E_rotation.setup_management_user_on_box`.

ВНИМАНИЕ: задача требует, чтобы worker умел резолвить SSH-хост сервера.
Сейчас `_dispatch_account_on_host` НЕ кладёт ни `ssh_host`, ни server.hostname
в payload, а `fetch_account_password` отдаёт только `{login, password}` —
worker делает fallback на `server_id` (`srv_<uuid>`), который не резолвится
ни в DNS, ни в /etc/hosts тестового стенда. Тесты падать будут именно на
этом шаге; находка зафиксирована в отчёте E.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration._helpers_E_rotation import (
    cleanup_user_on_box,
    create_account,
    create_server_pointing_at_target,
    decommission_server,
    ensure_it_admin,
    fetch_account_ciphertext,
    find_department_id,
    mark_server_managed,
    setup_management_user_on_box,
    user_password_works,
    wait_for_audit,
    wait_for_task,
)


# ── Session-scope: один setup на бокс на весь suite ─────────────────────────


@pytest.fixture(scope="session")
def _box_setup_done(ssh_test_host, ssh_session) -> None:
    """Один раз за сессию: завести dbos + sudoers + authorized_keys на боксе."""
    setup_management_user_on_box(ssh_session, ssh_test_host)


@pytest.fixture(scope="session")
def _it_admin_creds(
    auth_client: httpx.Client,
    admin_token: str,
    make_user,
    login_token,
) -> dict:
    """Один it-dept admin на всю сессию (создание юзера — дорогая операция)."""
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
    """Per-test managed-сервер, готовый к rotate/provision."""
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
# Точечная ротация (?server_id=X) — single mode
# ════════════════════════════════════════════════════════════════════════════


def test_rotate_password_single_server_e2e(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    worker_db_engine,
    loging_db_engine,
    ssh_session,
    ssh_test_host: dict,
):
    """Точечная ротация: worker SSH-chpasswd + ciphertext в server_service обновлён.

    Шаги:
      1. Создаём OS-юзера на боксе через provision (нужен реальный login на
         коробке, чтобы chpasswd был осмыслен).
      2. Снимаем исходный ciphertext.
      3. POST /rotate?server_id=<srv> → 202 + task_id.
      4. Ждём task=succeeded.
      5. Ciphertext обновился (отличается от исходного).
      6. Audit `server_account.password_rotate` success.
    """
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_rot_{e_managed_server['suffix']}"

    try:
        account = create_account(
            server_client, token, server_id,
            login=login, password="InitPass1!",
        )
        account_id = account["id"]

        # Provision на боксе — без этого chpasswd упадёт (нет юзера).
        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/provision",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, f"provision failed: {r.status_code} {r.text}"
        prov_task_id = r.json()["task_id"]
        prov_task = wait_for_task(worker_db_engine, prov_task_id, timeout=45.0)
        assert prov_task["status"] == "succeeded", (
            f"provision did not succeed: {prov_task}"
        )

        before = fetch_account_ciphertext(server_db_engine, account_id)
        assert before is not None, "account should have stored password"

        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/rotate",
            params={"server_id": server_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, f"rotate dispatch failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["mode"] == "single"
        assert len(body["tasks"]) == 1
        task_id = body["tasks"][0]["task_id"]

        task = wait_for_task(worker_db_engine, task_id, timeout=45.0)
        assert task["status"] == "succeeded", (
            f"rotate task failed: status={task['status']} err={task['last_error']}"
        )

        after = fetch_account_ciphertext(server_db_engine, account_id)
        assert after is not None
        assert after != before, "ciphertext should have changed after rotate"

        wait_for_audit(
            loging_db_engine,
            action="server_account.password_rotate",
            status="success",
            target_id=account_id,
        )
    finally:
        cleanup_user_on_box(ssh_session, login)


def test_rotate_single_server_decommissioned_409(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    loging_db_engine,
):
    """Точечная ротация на списанный сервер → 409 SERVER_DECOMMISSIONED."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]
    login = f"e_rot_dec_{e_managed_server['suffix']}"
    account = create_account(
        server_client, token, server_id, login=login,
    )
    account_id = account["id"]
    decommission_server(server_db_engine, server_id)

    r = server_client.post(
        f"/api/server/v1/server-accounts/{account_id}/rotate",
        params={"server_id": server_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409, f"expected 409, got {r.status_code} {r.text}"
    body = r.json()
    assert body.get("error_code") == "SERVER_DECOMMISSIONED"


# ════════════════════════════════════════════════════════════════════════════
# Массовая ротация (без server_id) — all mode
# ════════════════════════════════════════════════════════════════════════════


def _make_extra_managed_server(
    server_client, token, dept_id, server_db_engine, suffix,
) -> str:
    """Доп. managed-сервер в том же отделе для multi-link сценариев."""
    s = create_server_pointing_at_target(
        server_client, token, dept_id, suffix=suffix,
    )
    mark_server_managed(server_db_engine, s["id"])
    return s["id"]


def test_rotate_password_mass_all_dispatched(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    worker_db_engine,
    loging_db_engine,
    ssh_session,
):
    """Массовая ротация на 2 привязанных сервера → 2 task'и, обе queued."""
    token = e_managed_server["token"]
    server_id_1 = e_managed_server["id"]
    dept_id = e_managed_server["dept_id"]
    suffix = e_managed_server["suffix"]

    server_id_2 = _make_extra_managed_server(
        server_client, token, dept_id, server_db_engine, suffix=f"{suffix}_b",
    )

    login = f"e_mass_{suffix}"
    try:
        # Создаём аккаунт сразу на обоих серверах.
        r = server_client.post(
            "/api/server/v1/server-accounts",
            json={
                "server_ids": [server_id_1, server_id_2],
                "login": login,
                "password": "InitPass1!",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, f"create acct failed: {r.text}"
        account_id = r.json()["id"]

        # Mass-rotate без server_id.
        r = server_client.post(
            f"/api/server/v1/server-accounts/{account_id}/rotate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, f"mass rotate failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["mode"] == "all"
        assert len(body["tasks"]) == 2, f"expected 2 tasks, got {body}"
        assert body["skipped"] == []
        dispatched_ids = {t["server_id"] for t in body["tasks"]}
        assert dispatched_ids == {server_id_1, server_id_2}

        wait_for_audit(
            loging_db_engine,
            action="server_account.rotate_password_dispatch",
            status="success",
            target_id=account_id,
        )
        events = wait_for_audit(
            loging_db_engine,
            action="server_account.rotate_password_dispatch",
            status="success",
            target_id=account_id,
        )
        details = events["details"]
        assert details.get("mode") == "all"
        assert details.get("dispatched") == 2
        assert details.get("skipped_count", 0) == 0
    finally:
        cleanup_user_on_box(ssh_session, login)


def test_rotate_password_mass_one_decommissioned_continues(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
    loging_db_engine,
):
    """Один decommissioned среди привязанных → пропускается, остальные dispatched."""
    token = e_managed_server["token"]
    server_id_1 = e_managed_server["id"]
    dept_id = e_managed_server["dept_id"]
    suffix = e_managed_server["suffix"]

    server_id_2 = _make_extra_managed_server(
        server_client, token, dept_id, server_db_engine, suffix=f"{suffix}_dec",
    )

    r = server_client.post(
        "/api/server/v1/server-accounts",
        json={
            "server_ids": [server_id_1, server_id_2],
            "login": f"e_partial_{suffix}",
            "password": "InitPass1!",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]

    decommission_server(server_db_engine, server_id_2)

    r = server_client.post(
        f"/api/server/v1/server-accounts/{account_id}/rotate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, f"mass rotate failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["mode"] == "all"
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["server_id"] == server_id_1
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["server_id"] == server_id_2
    assert body["skipped"][0]["reason"] == "decommissioned"

    event = wait_for_audit(
        loging_db_engine,
        action="server_account.rotate_password_dispatch",
        status="success",
        target_id=account_id,
    )
    details = event["details"]
    assert details["dispatched"] == 1
    assert details["skipped_count"] == 1


def test_rotate_password_mass_all_decommissioned_409(
    e_managed_server: dict,
    server_client: httpx.Client,
    server_db_engine,
):
    """Если все привязанные сервера decommissioned — массовая ротация 409."""
    token = e_managed_server["token"]
    server_id = e_managed_server["id"]

    r = server_client.post(
        "/api/server/v1/server-accounts",
        json={
            "server_ids": [server_id],
            "login": f"e_alldec_{e_managed_server['suffix']}",
            "password": "InitPass1!",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]

    decommission_server(server_db_engine, server_id)

    r = server_client.post(
        f"/api/server/v1/server-accounts/{account_id}/rotate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409, f"expected 409, got {r.status_code} {r.text}"
    assert r.json().get("error_code") == "SERVER_DECOMMISSIONED"
