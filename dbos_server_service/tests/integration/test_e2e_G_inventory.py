"""E2E G-кластер: inventory.sync + users.inventory + warn-on-drift.

Полный стек (compose) с openssh-target. Каждый тест:
  1. Создаёт изолированный сервер в departement `it` под admin-ролью
     `server_service`, выкручивая bootstrap-flow F (prepare → is_managed=True)
     только там, где это нужно — managed-режим заходит на бокс по ключу под
     пользователем `dbos`, который кладёт worker'овский `prepare`-таск.
  2. Выставляет нужное состояние на боксе через `ssh_session` (sudo + useradd /
     usermod / userdel).
  3. Дёргает соответствующий dispatch (`POST /servers/{id}/inventory/sync` или
     `POST /servers/{id}/users/inventory`), ждёт worker SUCCEEDED.
  4. Ассертит изменения в DB (server_service / loging) + audit-события.

Все тесты используют `reset_state` (TRUNCATE серверов / аккаунтов / тасок /
audit между тестами). Identity и dept catalog не трогаем — это session-state.
"""

from __future__ import annotations

import time

import pytest

from tests.integration._helpers_G_inventory import (
    assert_audit_event,
    box_set_sudo,
    box_useradd,
    box_userdel,
    create_account,
    dispatch_inventory_sync,
    dispatch_prepare,
    dispatch_users_inventory,
    find_audit_event,
    get_account_row_by_login,
    get_link_row,
    get_os_version_row,
    get_server_row,
    register_server,
    setup_dept_admin,
    wait_task_status,
)


# ── Session-scoped: dept-admin identity (один на весь G-сьют) ──────────────

@pytest.fixture(scope="session")
def g_admin(auth_client, admin_token, make_user, login_token, integration_stack):
    """Юзер с server_service admin в отделе `it`. Создаётся один раз на сессию.

    Дальше каждый тест чистит данные через `reset_state`, но identity
    переживает — auth_db не reset'ится.
    """
    return setup_dept_admin(auth_client, admin_token, make_user, login_token)


# ── Per-test: создаём сервер с уникальным hostname/IP ───────────────────────

@pytest.fixture
def fresh_server(server_client, g_admin, ssh_test_host, reset_state):
    """Зарегистрировать сервер (НЕ prepared) с SSH-таргетом из compose.

    `reset_state` гарантирует пустую таблицу servers перед тестом — поэтому
    можно даже не рандомить host/ip жёстко, но мы всё равно генерим, чтобы
    тест не падал, если когда-нибудь параллелизация поедет в одну БД.
    """
    server = register_server(
        server_client, g_admin["token"],
        department_id=g_admin["department_id"],
        ssh_port=ssh_test_host["port"],
    )
    return server


@pytest.fixture
def prepared_server(server_client, worker_db_engine, server_db_engine, g_admin, ssh_test_host, fresh_server):
    """fresh_server + успешно отработанный F.prepare → is_managed=True.

    Используется managed-сценариями (inventory под dbos-mgmt user'ом).
    """
    task_id = dispatch_prepare(
        server_client, g_admin["token"],
        server_id=fresh_server["id"],
        username=ssh_test_host["root_login"],
        password=ssh_test_host["root_password"],
    )
    wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=90)
    row = get_server_row(server_db_engine, fresh_server["id"])
    assert row is not None and row["is_managed"] is True, (
        f"prepare flow did not flip is_managed=True: row={row!r}"
    )
    return fresh_server


# ──────────────────────────────────────────────────────────────────────────────
# 1. Hardware inventory.sync
# ──────────────────────────────────────────────────────────────────────────────


class TestHardwareInventorySync:
    """`POST /servers/{id}/inventory/sync` — SSH → lscpu/free/os-release."""

    def test_hardware_facts_landed_in_db(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        """Worker снимает CPU/OS через SSH под управляющим юзером и пишет в DB."""
        server_id = prepared_server["id"]

        task_id = dispatch_inventory_sync(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=90)

        server_row = get_server_row(server_db_engine, server_id)
        assert server_row is not None
        # cpu_cores обязателен в InventoryCallbackRequest (ge=1) → должен
        # появиться. os_version_id тоже — _resolve_or_create_os отрабатывает
        # при любом непустом payload.os_version.
        assert server_row["cpu_cores"] is not None and server_row["cpu_cores"] >= 1
        assert server_row["os_version_id"] is not None
        assert server_row["os_last_synced_at"] is not None

        # Audit: callback (server_service эмитит server.inventory_received на
        # success — это пишет server_service после receive_inventory).
        assert_audit_event(
            loging_db_engine,
            action="server.inventory_received",
            server_id=server_id,
            severity="INFO",
            timeout_s=15,
        )

    def test_os_version_auto_created_warns(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        """Box → DB актуализация OS-версии: имя из /etc/os-release заводится
        в `os_versions` каталоге и поднимает WARNING `os_version.create`.
        """
        server_id = prepared_server["id"]
        task_id = dispatch_inventory_sync(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=90)

        server_row = get_server_row(server_db_engine, server_id)
        assert server_row is not None and server_row["os_version_id"] is not None
        osv = get_os_version_row(server_db_engine, os_id=server_row["os_version_id"])
        assert osv is not None, "linked os_version not in os_versions catalog"
        assert osv["name"], "os_version.name is empty"

        # Auto-create — WARNING audit с reason=auto_from_inventory.
        ev = assert_audit_event(
            loging_db_engine,
            action="os_version.create",
            severity="WARNING",
            timeout_s=15,
        )
        assert ev["details"].get("reason") == "auto_from_inventory"
        assert ev["details"].get("name") == osv["name"]

    def test_os_version_relinked_no_recreate(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        """Второй inventory.sync видит уже существующий os_version — relink
        без повторного auto_from_inventory."""
        server_id = prepared_server["id"]

        t1 = dispatch_inventory_sync(server_client, g_admin["token"], server_id=server_id)
        wait_task_status(worker_db_engine, t1, target="succeeded", timeout_s=90)
        row1 = get_server_row(server_db_engine, server_id)
        assert row1 is not None
        os_id_first = row1["os_version_id"]
        assert os_id_first is not None

        # Запоминаем число WARNING-аудитов до второго прохода.
        from sqlalchemy import text
        with loging_db_engine.connect() as conn:
            warns_before = conn.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'os_version.create' AND severity = 'WARNING' "
                    "AND details ->> 'reason' = 'auto_from_inventory'"
                )
            ).scalar() or 0

        t2 = dispatch_inventory_sync(server_client, g_admin["token"], server_id=server_id)
        wait_task_status(worker_db_engine, t2, target="succeeded", timeout_s=90)

        row2 = get_server_row(server_db_engine, server_id)
        assert row2 is not None and row2["os_version_id"] == os_id_first, (
            "OS-version id changed between two inventory.sync runs"
        )

        # WARNING-аудит auto_from_inventory НЕ вырос — версия уже есть в каталоге.
        with loging_db_engine.connect() as conn:
            warns_after = conn.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'os_version.create' AND severity = 'WARNING' "
                    "AND details ->> 'reason' = 'auto_from_inventory'"
                )
            ).scalar() or 0
        assert warns_after == warns_before, (
            f"unexpected auto_from_inventory warns: before={warns_before} "
            f"after={warns_after}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Users inventory baseline (passwd + UID_MIN filter)
# ──────────────────────────────────────────────────────────────────────────────


class TestUsersInventoryBaseline:
    """`POST /servers/{id}/users/inventory` — getent passwd + UID_MIN filter."""

    def test_users_inventory_succeeds_and_audits(
        self,
        server_client,
        worker_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        """Empty-link state: ни одного аккаунта в БД, на боксе только seed-юзеры.
        Ожидаем successful audit `users_inventory_received` без drift'ов.
        """
        server_id = prepared_server["id"]
        task_id = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=60)

        ev = assert_audit_event(
            loging_db_engine,
            action="server_account.users_inventory_received",
            server_id=server_id,
            severity="INFO",
            timeout_s=15,
        )
        assert ev["status"] == "success"
        # На свежем боксе живут seed-юзеры (`dbosroot`, `dbos`) — оба пройдут
        # UID_MIN. Поэтому `found` >= 1.
        assert ev["details"].get("found", 0) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 3. Drift сценарии (warn-on-drift)
# ──────────────────────────────────────────────────────────────────────────────


class TestDriftAttributes:
    """Сценарий (а): на боксе has_sudo=True, в БД — False → drift=attributes,
    БД НЕ перетёрта."""

    def test_attribute_drift_emits_warning_and_db_unchanged(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        ssh_test_host,
        ssh_session,
        prepared_server,
    ):
        server_id = prepared_server["id"]
        login = f"gdrift_{int(time.time())}"

        # 1. Создать аккаунт в БД (has_sudo=False)
        acc = create_account(
            server_client, g_admin["token"],
            server_id=server_id, login=login,
            password="StrongPass1!",
            has_sudo=False,
        )

        # 2. Сделать пользователя на боксе с has_sudo=True
        with ssh_session() as ssh:
            box_useradd(
                ssh,
                sudo_password=ssh_test_host["root_password"],
                login=login,
                has_sudo=True,
            )

        # 3. Гонка users.inventory — worker увидит has_sudo=True на боксе.
        task_id = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=60)

        # 4. Audit: drift_detected WARNING, fields=["has_sudo"].
        ev = assert_audit_event(
            loging_db_engine,
            action="server_account.drift_detected",
            server_id=server_id,
            severity="WARNING",
            login=login,
            timeout_s=15,
        )
        assert ev["details"].get("drift") == "attributes"
        fields = ev["details"].get("fields") or []
        assert "has_sudo" in fields, f"fields missing has_sudo: {fields!r}"
        # expected/found должны отражать «БД=False, бокс=True».
        expected = ev["details"].get("expected") or {}
        found = ev["details"].get("found") or {}
        assert expected.get("has_sudo") is False
        assert found.get("has_sudo") is True

        # 5. БД НЕ изменена — has_sudo в записи остался False.
        db_row = get_account_row_by_login(
            server_db_engine,
            department_id=g_admin["department_id"], login=login,
        )
        assert db_row is not None and db_row["id"] == acc["id"]
        assert db_row["has_sudo"] is False, (
            "DB has_sudo overwritten by inventory — must stay source-of-truth"
        )

        # Cleanup — иначе следующий тест увидит мусор на боксе.
        with ssh_session() as ssh:
            box_userdel(
                ssh,
                sudo_password=ssh_test_host["root_password"],
                login=login,
            )


class TestDriftUnknownLogin:
    """Сценарий (б): юзер на боксе, в БД нет → drift=unknown_login,
    создаётся `source=discovered` record c password_encrypted=NULL."""

    def test_unknown_login_creates_discovered_record(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        ssh_test_host,
        ssh_session,
        prepared_server,
    ):
        server_id = prepared_server["id"]
        login = f"gunknown_{int(time.time())}"

        # 1. Создать юзера ТОЛЬКО на боксе.
        with ssh_session() as ssh:
            box_useradd(
                ssh,
                sudo_password=ssh_test_host["root_password"],
                login=login,
                has_sudo=False,
            )

        # 2. Inventory.
        task_id = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=60)

        # 3. Audit drift=unknown_login WARNING.
        ev = assert_audit_event(
            loging_db_engine,
            action="server_account.drift_detected",
            server_id=server_id,
            severity="WARNING",
            login=login,
            drift="unknown_login",
            timeout_s=15,
        )
        assert ev["details"]["drift"] == "unknown_login"

        # 4. В БД появилась запись с source=discovered, без пароля.
        db_row = get_account_row_by_login(
            server_db_engine,
            department_id=g_admin["department_id"], login=login,
        )
        assert db_row is not None, "discovered account not created in DB"
        assert db_row["source"] == "discovered"
        assert db_row["password_encrypted"] is None, (
            "discovered account should have password_encrypted=NULL"
        )

        # Cleanup.
        with ssh_session() as ssh:
            box_userdel(
                ssh,
                sudo_password=ssh_test_host["root_password"],
                login=login,
            )


class TestDriftMissingOnBox:
    """Сценарий (в): linked в БД, нет на боксе → drift=missing_on_box,
    `present_on_server=False`, запись НЕ удалена."""

    def test_missing_on_box_flips_present_but_keeps_record(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        server_id = prepared_server["id"]
        login = f"gmissing_{int(time.time())}"

        # 1. Создаём аккаунт в БД, привязанный к серверу. На боксе его
        #    не создаём — это и есть «missing».
        acc = create_account(
            server_client, g_admin["token"],
            server_id=server_id, login=login,
            password="StrongPass1!",
        )

        # 2. Inventory.
        task_id = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, task_id, target="succeeded", timeout_s=60)

        # 3. Audit drift=missing_on_box.
        ev = assert_audit_event(
            loging_db_engine,
            action="server_account.drift_detected",
            server_id=server_id,
            severity="WARNING",
            login=login,
            drift="missing_on_box",
            timeout_s=15,
        )
        assert ev["details"]["drift"] == "missing_on_box"

        # 4. Link: present_on_server=False, запись НЕ удалена.
        link = get_link_row(
            server_db_engine, account_id=acc["id"], server_id=server_id,
        )
        assert link is not None, "link must NOT be deleted on missing_on_box"
        assert link["present_on_server"] is False, (
            "missing_on_box drift must flip present_on_server=False"
        )

        # Аккаунт в server_accounts тоже жив.
        db_row = get_account_row_by_login(
            server_db_engine,
            department_id=g_admin["department_id"], login=login,
        )
        assert db_row is not None, "account row must not be deleted"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Re-emit на каждом сканировании (нет dedup на стороне server_service)
# ──────────────────────────────────────────────────────────────────────────────


class TestDriftReEmit:
    """Drift'ы re-emit'ятся на каждом сканировании — server_service не делает
    dedup. Suppress — кандидат в loging rule, gated."""

    def test_missing_on_box_drift_re_emitted_each_scan(
        self,
        server_client,
        worker_db_engine,
        server_db_engine,
        loging_db_engine,
        g_admin,
        prepared_server,
    ):
        server_id = prepared_server["id"]
        login = f"greemit_{int(time.time())}"

        create_account(
            server_client, g_admin["token"],
            server_id=server_id, login=login,
            password="StrongPass1!",
        )

        from sqlalchemy import text

        def _drift_count() -> int:
            with loging_db_engine.connect() as conn:
                return conn.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'server_account.drift_detected' "
                        "AND severity = 'WARNING' "
                        "AND details ->> 'login' = :login "
                        "AND details ->> 'drift' = 'missing_on_box'"
                    ),
                    {"login": login},
                ).scalar() or 0

        # Первый scan
        t1 = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, t1, target="succeeded", timeout_s=60)
        # Подождём чтобы первый drift приехал в loging.
        find_audit_event(
            loging_db_engine,
            action="server_account.drift_detected",
            server_id=server_id, login=login, drift="missing_on_box",
            timeout_s=15,
        )
        c1 = _drift_count()
        assert c1 >= 1, f"first scan: expected drift count >=1, got {c1}"

        # Второй scan — drift должен пере-эмититься.
        t2 = dispatch_users_inventory(
            server_client, g_admin["token"], server_id=server_id,
        )
        wait_task_status(worker_db_engine, t2, target="succeeded", timeout_s=60)

        deadline = time.monotonic() + 15.0
        c2 = c1
        while time.monotonic() < deadline:
            c2 = _drift_count()
            if c2 > c1:
                break
            time.sleep(0.5)

        assert c2 > c1, (
            f"drift not re-emitted on second scan: c1={c1} c2={c2} "
            f"(re-emit per scan is the documented contract — see "
            f"receive_users_inventory docstring)"
        )
