"""Тесты propagation host/ssh_port из payload в SSH-сессию.

`apply_session_hints(creds, payload)` должна прокидывать `host` и `ssh_port`
из task-payload в credentials ДО построения SshClient. Это важно, чтобы
worker не делал DNS-резолв по server_id, а использовал адрес, который
server_service уже разрезолвил при dispatch'е.

Тесты ставят монки на `asyncssh.connect` и проверяют, что kwargs.host
и kwargs.port соответствуют значениям из payload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.core.constants import TaskStatus
from src.tasks import inventory, users
from tests._ssh_mock_helpers import run_result as _run_result


def _inventory_conn():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=[
        _run_result("srv-01\n"),
        _run_result("Linux\n"),
        _run_result('{"lscpu":[]}'),
        _run_result('{"blockdevices":[]}'),
        _run_result("Filesystem Mounted 1B-blocks Used Use%\n"),
        _run_result("MemTotal:       16307128 kB\n"),
        _run_result("2: ens192: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"),
        _run_result('NAME="Test"\n'),
        _run_result(""),
        _run_result("1.7.5\n"),
        _run_result("Смоленск\n"),
        _run_result(""),
    ])
    return conn


def _users_conn():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=[
        _run_result("user1:x:1001:1001::/home/user1:/bin/bash\n"),
        _run_result("users:x:100:user1\n"),
        _run_result("UID_MIN\t1000\n"),
    ])
    return conn


class TestInventoryHostPropagation:
    async def test_host_from_payload_reaches_asyncssh(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_inv_h1",
            payload={
                "server_id": "srv_inv_h1",
                "host": "192.168.10.5",
                "ssh_port": 2222,
            },
        )
        conn = _inventory_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts",
            fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        kwargs = connect_mock.await_args.kwargs
        assert kwargs["host"] == "192.168.10.5"
        assert kwargs["port"] == 2222

    async def test_host_from_payload_used_when_no_account_id(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Без account_id creds берутся из payload (ssh_login или root).
        # host/ssh_port всё равно должны прийти из payload.
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_inv_h2",
            payload={
                "server_id": "srv_inv_h2",
                "ssh_login": "root",
                "host": "10.5.5.5",
                "ssh_port": 22,
            },
        )
        conn = _inventory_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts",
            fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        kwargs = connect_mock.await_args.kwargs
        assert kwargs["host"] == "10.5.5.5"
        assert kwargs["port"] == 22

    async def test_fetch_creds_host_takes_priority_over_payload(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Если fetch_account_password уже вернул host в credentials —
        # payload его не перетирает (это контракт apply_session_hints).
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_inv_h3",
            payload={
                "server_id": "srv_inv_h3",
                "account_id": "acc_h3",
                "host": "payload.host.addr",  # этот не должен использоваться
            },
        )

        async def fake_fetch(*a, **kw):
            return {"login": "ops", "password": "p", "host": "creds.host.addr"}

        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.fetch_account_password",
            fake_fetch,
        )

        conn = _inventory_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts",
            fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        kwargs = connect_mock.await_args.kwargs
        # credentials host приоритетнее payload host.
        assert kwargs["host"] == "creds.host.addr"


class TestUsersInventoryHostPropagation:
    async def test_host_from_payload_reaches_asyncssh(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_usr_h1",
            payload={
                "server_id": "srv_usr_h1",
                "host": "172.16.0.7",
                "ssh_port": 2222,
            },
        )
        conn = _users_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory",
            fake_submit,
        )

        await users.users_inventory.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        kwargs = connect_mock.await_args.kwargs
        assert kwargs["host"] == "172.16.0.7"
        assert kwargs["port"] == 2222

    async def test_no_host_in_payload_falls_back_to_server_id(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Без host в payload и без host в creds — _extract_host возвращает server_id.
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_usr_h2",
            payload={"server_id": "srv_usr_h2"},
        )
        conn = _users_conn()
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory",
            fake_submit,
        )

        await users.users_inventory.original_func(tid)

        kwargs = connect_mock.await_args.kwargs
        # fallback: host = server_id
        assert kwargs["host"] == "srv_usr_h2"
