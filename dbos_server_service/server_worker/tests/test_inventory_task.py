"""Integration-тесты `tasks/inventory.py` поверх реального SshClient (mock'нутого
через asyncssh.connect) и реального submit-callback'а в server_service.

Это не подмена unit-тестов `services/ssh_client.py`-фасада (там
monkeypatch'ится сам `collect_inventory`), а end-to-end проверка, что
handler корректно склеивает credentials → SshClient → submit_inventory_facts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh

from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.tasks import inventory
from tests._ssh_mock_helpers import run_result as _run_result


def _conn_with_inventory_output():
    """SSHClientConnection-like mock с canned-результатами inventory-команд.

    Очерёдность: hostname, uname, lscpu, lsblk, df, meminfo, net-interfaces,
    os-release, lspci, build_version, astra_license, apt-sources.
    """
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=[
        _run_result("srv-01\n"),
        _run_result("Linux srv-01 5.15.0-91-generic ...\n"),
        _run_result('{"lscpu":[{"field":"Architecture:","data":"x86_64"}]}'),
        _run_result(
            '{"blockdevices":[{"name":"sda","size":"500107862016","type":"disk",'
            '"model":"X","serial":"S1","children":[{"name":"sda1","type":"part",'
            '"mountpoint":"/"}]}]}'
        ),
        _run_result(
            "Filesystem Mounted 1B-blocks Used Use%\n"
            "/dev/sda1 / 500107862016 100021572403 20%\n"
        ),
        _run_result("MemTotal:       16307128 kB\nMemFree: 100 kB\n"),
        _run_result(
            "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536\n"
            "2: ens192: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
        ),
        _run_result('NAME="Astra Linux"\nVERSION_ID="1.7"\n'),
        _run_result('00:00.0 "Host bridge" "Intel"\n'),
        _run_result("1.7.5\n"),
        _run_result("Настоящая лицензия ... Смоленск ...\n"),
        _run_result("deb http://dl.astralinux.ru/ smolensk main\n# disabled\n\n"),
    ])
    return conn


class TestInventoryHappyPath:
    async def test_collects_inventory_and_submits_via_callback(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )

        # 1. fetch_account_password — отдаёт login/password/host.
        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "p", "host": "10.0.0.5"}

        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.fetch_account_password", fake_fetch,
        )

        # 2. asyncssh.connect — отдаёт canned inventory.
        conn = _conn_with_inventory_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        # 3. submit_inventory_facts — capture call args.
        submit_calls = []
        async def fake_submit(server_id, payload, target_department_id=None):
            submit_calls.append((server_id, payload))
            return {"ok": True, "os_version_id": "os_1", "disks_upserted": 1}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts",
            fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["server_id"] == "srv_1"
        assert t.result["submit_status"] == "submitted"
        assert t.result["facts"]["hostname"]["stdout"] == "srv-01"
        assert t.result["facts"]["os"]["NAME"] == "Astra Linux"
        # submit получил flat-schema payload под InventoryCallbackRequest
        assert len(submit_calls) == 1
        assert submit_calls[0][0] == "srv_1"
        payload = submit_calls[0][1]
        assert payload["hostname"] == "srv-01"
        # cpu_model / cpu_brand могут быть None если lscpu отдал минимум —
        # в этом тестовом stub'е только Architecture, поэтому brand/model None.
        assert "cpu_brand" in payload
        assert "cpu_model" in payload
        assert payload["cpu_cores"] >= 1
        assert "cpu_threads" in payload
        assert "cpu_frequency_ghz" in payload
        # Astra: os_version — только версия сборки, режим — отдельным полем.
        assert payload["os_version"] == "1.7.5"
        assert payload["os_security_mode"] == "Smolensk"
        # Только активная deb-строка, закомментированная отброшена.
        assert payload["repositories"] == ["deb http://dl.astralinux.ru/ smolensk main"]
        assert isinstance(payload["disks"], list)
        # Память: MemTotal 16307128 kB → 15925 МБ.
        assert payload["ram_total_mb"] == 16307128 // 1024
        # Сеть: активный ens192 без lo.
        assert payload["network_interfaces"] == ["ens192"]
        # Диск sda: раздел sda1 смонтирован в / → системный, занятость из df.
        assert len(payload["disks"]) == 1
        sda = payload["disks"][0]
        assert sda["name"] == "sda"
        assert sda["is_system"] is True
        assert sda["used_percent"] == 20.0
        assert sda["used_gb"] == 93  # 100021572403 B ≈ 93 ГБ
        # сырые facts больше не в payload — только flat-schema fields.
        assert "facts" not in payload

    async def test_submit_failure_does_not_fail_task(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_2",
            payload={"server_id": "srv_2", "ssh_login": "root"},
        )

        conn = _conn_with_inventory_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def boom(*a, **kw):
            raise CredentialFetchError(
                error_code="INVENTORY_SUBMIT_REJECTED",
                message="server_service returned 404",
            )
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", boom,
        )

        await inventory.inventory_sync.original_func(tid)
        t = await fetch_task(tid)
        # Task всё равно SUCCEEDED — facts в task.result, submit failed как warning.
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["submit_status"] == "submit_failed:INVENTORY_SUBMIT_REJECTED"
        assert "hostname" in t.result["facts"]


class TestInventoryAuthFailure:
    async def test_auth_fail_marks_task_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_3",
            payload={"server_id": "srv_3"},
        )
        # max_attempts=1 → terminal FAILED после одной ошибки.
        async with AsyncSessionLocal() as session:
            await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
            await session.commit()

        # Patch _schedule_retry на noop — защита от leak'ов background asyncio-task'ов.
        from src.tasks import _runner

        async def noop(*a, **kw):
            pass

        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        # asyncssh.connect → PermissionDenied
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=asyncssh.PermissionDenied(reason="bad")),
        )

        await inventory.inventory_sync.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "SSH_AUTH_FAILED" in t.last_error or "SshError" in t.last_error


class TestInventoryAuditDetails:
    async def test_facts_not_in_audit_details(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """facts намеренно ИСКЛЮЧЕНЫ из AUDIT_SAFE_FIELDS — публикуем только
        server_id и submit_status в audit, без объёмного inventory."""
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_4",
            payload={"server_id": "srv_4"},
        )

        conn = _conn_with_inventory_output()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"accepted_at": "now"}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts",
            fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        assert captured_audit, "audit must be emitted"
        details = captured_audit[0]["details"]
        emitted = details.get("result", {})
        # facts НЕ должны быть в audit
        assert "facts" not in emitted, f"facts leaked into audit: {emitted}"
        assert emitted.get("server_id") == "srv_4"
        assert emitted.get("submit_status") == "submitted"
