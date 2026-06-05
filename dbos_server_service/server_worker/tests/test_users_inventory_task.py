"""Тесты `tasks/users.py` (инвентаризация OS-пользователей) + парсер getent.

* `os_users_facts_to_payload` — UID-фильтр по UID_MIN, sudo по группам,
  отсев системных и невалидных login'ов;
* end-to-end handler: credentials → SshClient → submit_users_inventory,
  submit-фейл не роняет task'у.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh

from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.services import ssh_client
from src.tasks import users
from tests._ssh_mock_helpers import run_result as _run_result


_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1::/usr/sbin:/usr/sbin/nologin\n"
    "postgres:x:112:117::/var/lib/postgresql:/bin/bash\n"
    "ops:x:1001:1001:Ops:/home/ops:/bin/bash\n"
    "deploy:x:1002:1002::/home/deploy:/bin/sh\n"
    "bad user:x:1003:1003::/home/bad:/bin/bash\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
)
_GROUP = "root:x:0:\nsudo:x:27:ops\nops:x:1001:\ndeploy:x:1002:\n"
_LOGIN_DEFS = "# defaults\nUID_MIN\t1000\nUID_MAX\t60000\n"


def _conn_with_users_output():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=[
        _run_result(_PASSWD),
        _run_result(_GROUP),
        _run_result(_LOGIN_DEFS),
    ])
    return conn


# ── Parser ───────────────────────────────────────────────────────────────────


class TestOsUsersParser:
    def test_filters_system_users_by_uid_min(self):
        facts = {
            "passwd": {"stdout": _PASSWD, "returncode": 0},
            "group": {"stdout": _GROUP, "returncode": 0},
            "login_defs": {"stdout": _LOGIN_DEFS, "returncode": 0},
        }
        out = ssh_client.os_users_facts_to_payload(facts)
        logins = {u["login"] for u in out["users"]}
        # root/daemon/postgres (uid<1000), nobody (65534), "bad user" (regex) — out.
        assert logins == {"ops", "deploy"}

    def test_sudo_detected_from_group(self):
        facts = {
            "passwd": {"stdout": _PASSWD, "returncode": 0},
            "group": {"stdout": _GROUP, "returncode": 0},
            "login_defs": {"stdout": _LOGIN_DEFS, "returncode": 0},
        }
        out = ssh_client.os_users_facts_to_payload(facts)
        by_login = {u["login"]: u for u in out["users"]}
        assert by_login["ops"]["has_sudo"] is True
        assert by_login["ops"]["unix_groups"] == ["sudo"]
        assert by_login["deploy"]["has_sudo"] is False

    def test_uid_min_fallback_when_login_defs_missing(self):
        facts = {
            "passwd": {"stdout": "svc:x:1500:1500::/home/svc:/bin/bash\n", "returncode": 0},
            "group": {"stdout": "", "returncode": 0},
            "login_defs": {"error": "no such file", "returncode": 1},
        }
        out = ssh_client.os_users_facts_to_payload(facts)
        # fallback UID_MIN=1000 → svc (1500) проходит.
        assert {u["login"] for u in out["users"]} == {"svc"}

    def test_custom_uid_min_respected(self):
        facts = {
            "passwd": {"stdout": "early:x:500:500::/home/early:/bin/bash\n"
                                 "late:x:2000:2000::/home/late:/bin/bash\n", "returncode": 0},
            "group": {"stdout": "", "returncode": 0},
            "login_defs": {"stdout": "UID_MIN 1500\n", "returncode": 0},
        }
        out = ssh_client.os_users_facts_to_payload(facts)
        assert {u["login"] for u in out["users"]} == {"late"}

    def test_empty_passwd_yields_no_users(self):
        out = ssh_client.os_users_facts_to_payload({"passwd": {"error": "x"}})
        assert out == {"users": []}


# ── Handler ────────────────────────────────────────────────────────────────


class TestUsersInventoryHandler:
    async def test_collects_and_submits(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_u1",
            payload={"server_id": "srv_u1", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "p", "host": "10.0.0.5"}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password", fake_fetch,
        )

        conn = _conn_with_users_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, payload, target_department_id=None):
            submit_calls.append((server_id, payload))
            return {"ok": True, "created": 1, "updated": 1, "drifted": 0}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory", fake_submit,
        )

        await users.users_inventory.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["server_id"] == "srv_u1"
        assert t.result["submit_status"] == "submitted"
        assert t.result["user_count"] == 2
        assert len(submit_calls) == 1
        payload = submit_calls[0][1]
        assert {u["login"] for u in payload["users"]} == {"ops", "deploy"}

    async def test_submit_failure_does_not_fail_task(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_u2",
            payload={"server_id": "srv_u2", "ssh_login": "root"},
        )
        conn = _conn_with_users_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def boom(*a, **kw):
            raise CredentialFetchError(
                error_code="USERS_INVENTORY_SUBMIT_REJECTED",
                message="server_service returned 404",
            )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory", boom,
        )

        await users.users_inventory.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["submit_status"] == "submit_failed:USERS_INVENTORY_SUBMIT_REJECTED"

    async def test_users_not_in_audit_details(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_u3",
            payload={"server_id": "srv_u3"},
        )
        conn = _conn_with_users_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory", fake_submit,
        )

        await users.users_inventory.original_func(tid)

        assert captured_audit, "audit must be emitted"
        emitted = captured_audit[0]["details"].get("result", {})
        # Список пользователей НЕ должен утекать в audit, только счётчик.
        assert "users" not in emitted
        assert emitted.get("server_id") == "srv_u3"
        assert emitted.get("user_count") == 2
