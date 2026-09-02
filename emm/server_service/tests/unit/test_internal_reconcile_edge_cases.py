"""Edge cases для internal_service: receive_users_inventory + _resolve_or_create_os.

Покрывает сценарии, не закрытые test_users_inventory.py:
* Reconcile с пустым payload.users — только missing_on_box drift для всех привязанных.
* Partial drift: один атрибут отличается (только shell).
* Батч: linked_by_account_ids корректно обрабатывает аккаунт без активной связки
  (link is None → mark_link_inventoried не вызывается, present++ всё равно считается).
* _resolve_or_create_os: повторный вызов с тем же именем возвращает тот же id (no dup).
* _resolve_or_create_os: новое имя → WARNING audit + новый id.
* _account_attr_drift: все атрибуты одинаковые → пустой dict.
* _account_attr_drift: unix_groups как множество (порядок не важен) → нет drift.
* _account_attr_drift: только has_sudo отличается → один ключ в diff.
"""

from __future__ import annotations

import pytest

from src.services.internal_service import _account_attr_drift


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает вызовы audit_service.emit в internal_service и смежных модулях."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.internal_service.audit_service.emit",
        "src.services.server.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    )


# ── _account_attr_drift unit tests ───────────────────────────────────────────


class _FakeAccount:
    """Имитация ORM-объекта server_account для _account_attr_drift."""

    def __init__(
        self,
        *,
        has_sudo: bool = False,
        unix_groups: list | None = None,
        shell: str | None = "/bin/bash",
        home_dir: str | None = "/home/user",
    ):
        self.has_sudo = has_sudo
        self.unix_groups = unix_groups or []
        self.shell = shell
        self.home_dir = home_dir


class _FakeItem:
    """Имитация InventoryUser-item'а."""

    def __init__(
        self,
        *,
        has_sudo: bool = False,
        unix_groups: list | None = None,
        shell: str | None = "/bin/bash",
        home_dir: str | None = "/home/user",
    ):
        self.has_sudo = has_sudo
        self.unix_groups = unix_groups or []
        self.shell = shell
        self.home_dir = home_dir


class TestAccountAttrDrift:
    def test_no_drift_when_all_match(self):
        acc = _FakeAccount(has_sudo=True, unix_groups=["sudo", "wheel"],
                           shell="/bin/bash", home_dir="/home/ops")
        item = _FakeItem(has_sudo=True, unix_groups=["sudo", "wheel"],
                         shell="/bin/bash", home_dir="/home/ops")
        assert _account_attr_drift(acc, item) == {}

    def test_unix_groups_order_irrelevant(self):
        """Группы как множество: порядок из getent не совпадает с БД — не drift."""
        acc = _FakeAccount(unix_groups=["a", "b", "c"])
        item = _FakeItem(unix_groups=["c", "a", "b"])
        assert _account_attr_drift(acc, item) == {}

    def test_unix_groups_empty_vs_empty(self):
        acc = _FakeAccount(unix_groups=[])
        item = _FakeItem(unix_groups=[])
        assert _account_attr_drift(acc, item) == {}

    def test_unix_groups_none_vs_empty(self):
        """БД хранит None (legacy), бокс отдаёт [] — не должно быть drift."""
        acc = _FakeAccount(unix_groups=None)
        item = _FakeItem(unix_groups=[])
        assert _account_attr_drift(acc, item) == {}

    def test_only_has_sudo_differs(self):
        acc = _FakeAccount(has_sudo=False)
        item = _FakeItem(has_sudo=True)
        diff = _account_attr_drift(acc, item)
        assert set(diff.keys()) == {"has_sudo"}
        assert diff["has_sudo"]["expected"] is False
        assert diff["has_sudo"]["found"] is True

    def test_only_shell_differs(self):
        acc = _FakeAccount(shell="/bin/bash")
        item = _FakeItem(shell="/bin/sh")
        diff = _account_attr_drift(acc, item)
        assert set(diff.keys()) == {"shell"}
        assert diff["shell"]["expected"] == "/bin/bash"
        assert diff["shell"]["found"] == "/bin/sh"

    def test_home_dir_no_longer_counted_as_drift(self):
        """home_dir намеренно исключён из `_DRIFT_ATTRS` — PATCH home_dir не
        запускает fan-out (worker не двигает $HOME), и эмитить drift на каждом
        скане смысла нет (оператор не может его закрыть API-действием)."""
        acc = _FakeAccount(home_dir="/home/user")
        item = _FakeItem(home_dir="/var/lib/app")
        diff = _account_attr_drift(acc, item)
        assert diff == {}

    def test_only_unix_groups_differs(self):
        acc = _FakeAccount(unix_groups=["sudo"])
        item = _FakeItem(unix_groups=["wheel"])
        diff = _account_attr_drift(acc, item)
        assert set(diff.keys()) == {"unix_groups"}
        assert set(diff["unix_groups"]["expected"]) == {"sudo"}
        assert set(diff["unix_groups"]["found"]) == {"wheel"}

    def test_all_three_managed_attrs_differ(self):
        """home_dir намеренно не считается drift'ом (см. `_DRIFT_ATTRS`)."""
        acc = _FakeAccount(has_sudo=False, unix_groups=["a"],
                           shell="/bin/bash", home_dir="/home/x")
        item = _FakeItem(has_sudo=True, unix_groups=["b"],
                         shell="/bin/sh", home_dir="/home/y")
        diff = _account_attr_drift(acc, item)
        assert set(diff.keys()) == {"has_sudo", "unix_groups", "shell"}

    def test_added_group_to_box(self):
        """Бокс добавил группу, которой нет в БД."""
        acc = _FakeAccount(unix_groups=["sudo"])
        item = _FakeItem(unix_groups=["sudo", "extra"])
        diff = _account_attr_drift(acc, item)
        assert "unix_groups" in diff

    def test_removed_group_from_box(self):
        """Бокс потерял группу."""
        acc = _FakeAccount(unix_groups=["sudo", "wheel"])
        item = _FakeItem(unix_groups=["sudo"])
        diff = _account_attr_drift(acc, item)
        assert "unix_groups" in diff


# ── _resolve_or_create_os integration tests ──────────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestResolveOrCreateOs:
    """_resolve_or_create_os через inventory callback endpoint."""

    async def test_same_os_name_returns_same_id(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Два inventory-callback с одним именем ОС → один os_version_id, нет дублей."""
        srv1 = await make_server(department_id=dept_a)
        srv2 = await make_server(department_id=dept_a)

        def _inv(srv_id, os_name):
            return {
                "hostname": f"host-{srv_id[-4:]}",
                "kernel": "5.10.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": os_name, "disks": [],
            }

        BASE_INT = "/api/server/v1/internal"

        # Имя обязано пройти KNOWN_OS_PREFIXES — иначе запись не создаётся
        # (whitelist).
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv1.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json=_inv(srv1.id, "Astra Linux SE 1.7"),
        )
        assert r1.status_code == 200, r1.text
        os_id_first = r1.json()["os_version_id"]

        r2 = await client.post(
            f"{BASE_INT}/servers/{srv2.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json=_inv(srv2.id, "Astra Linux SE 1.7"),
        )
        assert r2.status_code == 200, r2.text
        os_id_second = r2.json()["os_version_id"]

        # Одно и то же имя → тот же id.
        assert os_id_first == os_id_second
        assert os_id_first is not None

        # Первый вызов создаёт запись (warning), второй — нет.
        os_creates = [
            e for e in captured_emits
            if e.get("action") == "os_version.create"
        ]
        assert len(os_creates) == 1
        assert os_creates[0]["details"]["name"] == "Astra Linux SE 1.7"

    async def test_different_os_names_give_different_ids(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv1 = await make_server(department_id=dept_a)
        srv2 = await make_server(department_id=dept_a)

        BASE_INT = "/api/server/v1/internal"

        def _inv(srv_id, os_name):
            return {
                "hostname": f"host-{srv_id[-4:]}", "kernel": "5.10.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": os_name, "disks": [],
            }

        r1 = await client.post(
            f"{BASE_INT}/servers/{srv1.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json=_inv(srv1.id, "Astra Linux SE 1.7"),
        )
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv2.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json=_inv(srv2.id, "Ubuntu 22.04"),
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["os_version_id"] != r2.json()["os_version_id"]

    async def test_new_os_warning_contains_server_info(
        self, client, worker_bot_token_a, make_server, dept_a, captured_emits,
    ):
        """WARNING-аудит os_version.create должен содержать server_id и dept."""
        srv = await make_server(department_id=dept_a)
        BASE_INT = "/api/server/v1/internal"

        await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={
                "hostname": f"host-{srv.id[-4:]}", "kernel": "5.10.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                # known prefix "Astra Linux" → пройдёт whitelist, создаст запись
                "os_version": "Astra Linux SE 9.99", "disks": [],
            },
        )

        warns = [
            e for e in captured_emits
            if e.get("action") == "os_version.create" and e.get("status") == "warning"
        ]
        assert len(warns) == 1
        d = warns[0]["details"]
        assert d["server_id"] == srv.id
        assert d["server_department_id"] == dept_a
        assert d["reason"] == "auto_from_inventory"


# ── receive_users_inventory: edge cases ──────────────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestUsersInventoryEdgeCases:
    """Граничные случаи reconcile в receive_users_inventory."""

    async def test_empty_payload_marks_all_linked_absent(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        """Пустой список users → все привязанные аккаунты помечаются отсутствующими."""
        srv = await make_server(department_id=dept_a)
        acc1 = await make_account(server_id=srv.id, login="alice")
        acc2 = await make_account(server_id=srv.id, login="bob")

        BASE_INT = "/api/server/v1/internal"
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={"users": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 0
        assert body["present"] == 0
        assert body["drifted"] == 2

        drift = [e for e in captured_emits if e.get("action") == "server_account.drift_detected"]
        assert len(drift) == 2
        logins = {e["details"]["login"] for e in drift}
        assert logins == {"alice", "bob"}
        assert all(e["details"]["drift"] == "missing_on_box" for e in drift)

    async def test_single_attribute_drift_only_shell(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        """Только shell отличается — drift-emit содержит ровно одно поле."""
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="sysop",
                           shell="/bin/bash", has_sudo=True)

        BASE_INT = "/api/server/v1/internal"
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={"users": [
                {"login": "sysop", "uid": 1500, "shell": "/bin/zsh",
                 "home_dir": None, "unix_groups": [], "has_sudo": True},
            ]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 1
        assert body["drifted"] == 1

        drift = [e for e in captured_emits if e.get("action") == "server_account.drift_detected"]
        assert len(drift) == 1
        assert drift[0]["details"]["drift"] == "attributes"
        assert drift[0]["details"]["fields"] == ["shell"]

    async def test_unknown_login_not_persisted_as_account(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        """Незнакомый логин аккаунтом в БД не оседает — уходит в unknown_users."""
        from sqlalchemy import select
        from src.models import ServerAccount

        srv = await make_server(department_id=dept_a)

        BASE_INT = "/api/server/v1/internal"
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={"users": [{"login": "newuser", "uid": 2000}]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 0
        assert len(body["unknown_users"]) == 1
        u = body["unknown_users"][0]
        assert u["login"] == "newuser"
        assert u["uid"] == 2000
        await db.commit()

        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "newuser")
        )).scalar_one_or_none()
        assert acc is None

    async def test_no_users_no_linked_no_drift(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Пустой payload и нет привязанных аккаунтов → 0 created, 0 present, 0 drifted."""
        srv = await make_server(department_id=dept_a)

        BASE_INT = "/api/server/v1/internal"
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={"users": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["created"] == 0
        assert body["present"] == 0
        assert body["drifted"] == 0
        # result_summary должен быть пустым по drift'ам.
        assert body["result_summary"]["total_users"] == 0
        assert body["result_summary"]["created_discovered"] == 0
        assert body["result_summary"]["drifts"] == []

        drift = [e for e in captured_emits if e.get("action") == "server_account.drift_detected"]
        assert drift == []

    async def test_reconcile_success_audit_counts_correct(
        self, client, worker_bot_token_a, make_server, make_account, dept_a,
        captured_emits,
    ):
        """Агрегированный success-аудит содержит правильные created/present/drifted."""
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="keep")

        BASE_INT = "/api/server/v1/internal"
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={"users": [
                {"login": "keep", "uid": 1001},
                {"login": "new_one", "uid": 1002},
            ]},
        )
        assert resp.status_code == 200

        success = [
            e for e in captured_emits
            if e.get("action") == "server_account.users_inventory_received"
            and e.get("status") == "success"
        ]
        assert len(success) == 1
        d = success[0]["details"]
        # new_one аккаунтом не заводится — created остаётся нулём.
        assert d["created"] == 0
        assert d["present"] == 1
        # new_one — незнакомый логин, дрейф unknown_login всё равно эмитится.
        assert d["drifted"] == 1
        assert d["found"] == 2
        assert d["department_id"] == dept_a
