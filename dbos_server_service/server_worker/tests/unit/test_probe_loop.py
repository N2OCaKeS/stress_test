"""Unit-тесты фоновых probe-циклов (`src/services/probe_loop.py`).

Проверяем: свежее чтение интервала/enabled из settings, фан-аут по целям с
вызовом правильных callback'ов (submit_power_state / submit_vm_state), пропуск
тика при disabled, устойчивость к недоступности settings, маппинг ВМ-payload.
"""

from __future__ import annotations

import asyncio

import pytest

from src.services import probe_loop, server_service_client


# ── _interval_and_enabled ────────────────────────────────────────────────────


async def test_interval_and_enabled_reads_fresh(monkeypatch):
    async def fake_settings():
        return {
            "reachability_probe_interval_seconds": 45,
            "power_probe_interval_seconds": 200,
            "reachability_probe_enabled": True,
            "power_probe_enabled": False,
        }

    monkeypatch.setattr(server_service_client, "get_probe_settings", fake_settings)

    interval, enabled = await probe_loop._interval_and_enabled("reachability")
    assert interval == 45.0
    assert enabled is True

    interval, enabled = await probe_loop._interval_and_enabled("power")
    assert interval == 200.0
    assert enabled is False


async def test_interval_and_enabled_settings_unavailable(monkeypatch):
    async def boom():
        raise RuntimeError("server_service down")

    monkeypatch.setattr(server_service_client, "get_probe_settings", boom)

    interval, enabled = await probe_loop._interval_and_enabled("reachability")
    assert interval == probe_loop._FALLBACK_REACHABILITY_INTERVAL
    assert enabled is False


async def test_interval_floor_applied(monkeypatch):
    async def fake_settings():
        # Ниже floor'а, но не 0/None — проверяем именно нижнюю границу.
        return {"reachability_probe_interval_seconds": 1,
                "reachability_probe_enabled": True}

    monkeypatch.setattr(server_service_client, "get_probe_settings", fake_settings)
    interval, _ = await probe_loop._interval_and_enabled("reachability")
    assert interval == probe_loop._MIN_INTERVAL


# ── _run_probes fan-out ──────────────────────────────────────────────────────


async def test_run_probes_fans_out_to_servers_and_vms(monkeypatch):
    async def fake_targets():
        return {
            "servers": [{"server_id": "srv_1"}, {"server_id": "srv_2"}],
            "vms": [{"vm_id": "vm_1"}],
        }

    monkeypatch.setattr(server_service_client, "get_probe_targets", fake_targets)

    server_calls: list[dict] = []
    vm_calls: list[dict] = []

    async def fake_server_probe(sem, target):
        server_calls.append(target)

    async def fake_vm_probe(sem, target):
        vm_calls.append(target)

    await probe_loop._run_probes(fake_server_probe, fake_vm_probe, kind="reachability")

    assert {c["server_id"] for c in server_calls} == {"srv_1", "srv_2"}
    assert {c["vm_id"] for c in vm_calls} == {"vm_1"}


async def test_run_probes_empty_no_calls(monkeypatch):
    async def fake_targets():
        return {"servers": [], "vms": []}

    monkeypatch.setattr(server_service_client, "get_probe_targets", fake_targets)

    calls: list = []

    async def fake_probe(sem, target):
        calls.append(target)

    await probe_loop._run_probes(fake_probe, fake_probe, kind="power")
    assert calls == []


# ── per-target probes ────────────────────────────────────────────────────────


async def test_server_reachability_submits_signals(monkeypatch):
    async def fake_reach(host, *, ssh_port, ping_timeout, tcp_timeout):
        return {
            "ping_reachable": True, "ping_latency_ms": 1.2,
            "ssh_reachable": False, "ssh_latency_ms": None,
        }

    monkeypatch.setattr(
        "src.tasks._reachability.probe_reachability_signals", fake_reach,
    )

    captured: dict = {}

    async def fake_submit(server_id, power_state, source, dept, **kwargs):
        captured.update(
            server_id=server_id, power_state=power_state, source=source,
            dept=dept, **kwargs,
        )
        return {"ok": True}

    monkeypatch.setattr(server_service_client, "submit_power_state", fake_submit)

    sem = asyncio.Semaphore(4)
    await probe_loop._probe_server_reachability(
        sem, {"server_id": "srv_1", "department_id": "dep_a",
              "host": "10.0.0.5", "ssh_port": 22},
    )

    # ping reachable → legacy power_state=on, source=ping; ipmi не трогаем.
    assert captured["server_id"] == "srv_1"
    assert captured["power_state"] == "on"
    assert captured["source"] == "ping"
    assert captured["ping_reachable"] is True
    assert captured["ssh_reachable"] is False
    assert captured["ipmi_power_state"] is None


async def test_server_reachability_swallows_probe_error(monkeypatch):
    async def boom(host, **kwargs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(
        "src.tasks._reachability.probe_reachability_signals", boom,
    )

    called = []

    async def fake_submit(*a, **k):
        called.append(1)

    monkeypatch.setattr(server_service_client, "submit_power_state", fake_submit)

    sem = asyncio.Semaphore(4)
    # Не должно бросать: сбой одной цели глотается.
    await probe_loop._probe_server_reachability(
        sem, {"server_id": "srv_1", "host": "10.0.0.5", "ssh_port": 22},
    )
    assert called == []


async def test_server_power_submits_ipmi(monkeypatch):
    async def fake_ipmi(server_id, dept):
        return "on"

    monkeypatch.setattr("src.tasks.power._probe_ipmi_power_state", fake_ipmi)

    captured: dict = {}

    async def fake_submit(server_id, power_state, source, dept, **kwargs):
        captured.update(
            server_id=server_id, power_state=power_state, source=source, **kwargs,
        )

    monkeypatch.setattr(server_service_client, "submit_power_state", fake_submit)

    sem = asyncio.Semaphore(4)
    await probe_loop._probe_server_power(
        sem, {"server_id": "srv_1", "department_id": "dep_a"},
    )
    assert captured["power_state"] == "on"
    assert captured["source"] == "bmc"
    assert captured["ipmi_power_state"] == "on"


# ── _vm_hub_payload mapping ──────────────────────────────────────────────────


def test_vm_hub_payload_mapping():
    target = {
        "vm_id": "vm_1", "vm_name": "guest-a", "department_id": "dep_a",
        "network_mode": "bridge", "guest_ip": "10.0.0.9",
        "hub_server_id": "srv_hub", "hub_host": "10.0.0.1", "hub_ssh_port": 2222,
        "hub_is_managed": True, "hub_management_user": "dbos",
    }
    payload = probe_loop._vm_hub_payload(target)
    assert payload["server_id"] == "srv_hub"
    assert payload["hub_server_id"] == "srv_hub"
    assert payload["host"] == "10.0.0.1"
    assert payload["ssh_port"] == 2222
    assert payload["management_user"] == "dbos"
    assert payload["vm_name"] == "guest-a"
    assert payload["name"] == "guest-a"
    assert payload["guest_ip"] == "10.0.0.9"
    assert payload["target_department_id"] == "dep_a"


# ── loop skip when disabled ──────────────────────────────────────────────────


async def test_loop_skips_probes_when_disabled(monkeypatch):
    async def disabled(kind):
        return 10.0, False

    monkeypatch.setattr(probe_loop, "_interval_and_enabled", disabled)

    ran: list = []

    async def fake_run(*a, **k):
        ran.append(1)

    monkeypatch.setattr(probe_loop, "_run_probes", fake_run)

    async def stop_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(probe_loop.asyncio, "sleep", stop_sleep)

    with pytest.raises(asyncio.CancelledError):
        await probe_loop.run_reachability_loop()
    assert ran == []


async def test_loop_runs_probes_when_enabled(monkeypatch):
    async def enabled(kind):
        return 10.0, True

    monkeypatch.setattr(probe_loop, "_interval_and_enabled", enabled)

    ran: list = []

    async def fake_run(server_probe, vm_probe, *, kind):
        ran.append(kind)

    monkeypatch.setattr(probe_loop, "_run_probes", fake_run)

    async def stop_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(probe_loop.asyncio, "sleep", stop_sleep)

    with pytest.raises(asyncio.CancelledError):
        await probe_loop.run_power_loop()
    assert ran == ["power"]
